"""Command line interface: `twinlens scan | apply | undo | demo`."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .cache import HashCache
from .cluster import build_clusters
from .config import Config, load_config
from .plan import PLAN_FORMAT, PlanError, build_plan, execute, prepare, read_json, undo, write_json
from .report import report_data, write_report
from .scanner import scan


# ── terminal helpers ────────────────────────────────────────────────────────
def _enable_ansi() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    return True


class Style:
    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.on else text

    def bold(self, t: str) -> str: return self._wrap("1", t)
    def dim(self, t: str) -> str: return self._wrap("2", t)
    def green(self, t: str) -> str: return self._wrap("32", t)
    def cyan(self, t: str) -> str: return self._wrap("36", t)
    def violet(self, t: str) -> str: return self._wrap("35", t)
    def red(self, t: str) -> str: return self._wrap("31", t)
    def yellow(self, t: str) -> str: return self._wrap("33", t)


S = Style(False)


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def display_path(path: Path) -> str:
    """Show paths under the home folder as ~/… so shared reports don't reveal the username."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _progress(done: int, total: int) -> None:
    if not sys.stderr.isatty() or total == 0:
        return
    width = 28
    filled = int(width * done / total)
    bar = "█" * filled + "░" * (width - filled)
    sys.stderr.write(f"\r  hashing {bar} {done}/{total}")
    if done == total:
        sys.stderr.write("\r" + " " * (width + 30) + "\r")
    sys.stderr.flush()


def _banner() -> None:
    print(S.bold(S.cyan("◯") + S.violet("◯") + " twinlens") + S.dim(f"  v{__version__}"))


# ── commands ────────────────────────────────────────────────────────────────
def cmd_scan(args: argparse.Namespace, cfg: Config) -> int:
    roots = [Path(p).expanduser().resolve() for p in args.paths]
    missing = [r for r in roots if not r.exists()]
    if missing:
        print(S.red(f"error: path not found: {missing[0]}"), file=sys.stderr)
        return 2
    _banner()
    out_dir = cfg.report_dir.resolve()
    started = time.perf_counter()
    with HashCache(None if args.no_cache else cfg.cache) as cache:
        result = scan(
            roots, cache, workers=cfg.effective_workers, progress=_progress,
            extra_skip=[out_dir, cfg.quarantine.resolve()],
        )
    clusters, comparisons = build_clusters(result.records, cfg.threshold)
    seconds = time.perf_counter() - started

    n = len(result.records)
    summary = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "roots": [display_path(r) for r in roots],
        "photos": n,
        "bytes": sum(r.size for r in result.records),
        "threshold": cfg.threshold,
        "seconds": round(seconds, 3),
        "comparisons": comparisons,
        "naive_comparisons": n * (n - 1) // 2,
        "cache_hits": result.cache_hits,
        "errors": len(result.errors),
    }
    plan = build_plan(clusters, roots, cfg.threshold)
    plan_path = out_dir / "plan.json"
    write_json(plan_path, plan)
    if args.json:
        write_json(Path(args.json), {
            "summary": summary,
            "errors": [{"path": p, "error": e} for p, e in result.errors],
            "clusters": [
                {"id": c.id, "kind": c.kind, "members": [
                    {**m.record.to_dict(), "keep": m.keep, "score": m.score, "distance": m.distance}
                    for m in c.members
                ]} for c in clusters
            ],
        })

    dupes = sum(len(c.members) - 1 for c in clusters)
    reclaim = sum(c.reclaimable for c in clusters)
    naive = summary["naive_comparisons"]
    print(f"  {S.bold(str(n))} photos in {seconds:.1f}s "
          + S.dim(f"({result.cache_hits} cached, {comparisons:,} comparisons vs {naive:,} brute force)"))
    if not clusters:
        print(S.green("  ✓ no duplicates found — this library is already clean"))
    else:
        print(f"  {S.bold(str(len(clusters)))} twin groups · {S.bold(str(dupes))} removable copies · "
              + S.green(S.bold(human_bytes(reclaim) + " to reclaim")))
        print()
        for c in clusters[: args.top]:
            keeper = c.keeper
            tag = S.yellow("exact  ") if c.kind == "exact" else S.cyan("similar")
            print(f"  {tag} #{c.id:<3} keep {S.bold(Path(keeper.record.path).name)}"
                  + S.dim(f"  ({', '.join(keeper.reasons) or 'best score'})")
                  + f"  {S.green('−' + human_bytes(c.reclaimable))}")
            for m in c.members:
                if not m.keep:
                    name, size = Path(m.record.path).name, human_bytes(m.record.size)
                    print(S.dim(f"            ↳ {name}  Δ{m.distance}  {size}"))
        if len(clusters) > args.top:
            print(S.dim(f"  … and {len(clusters) - args.top} more groups in the report"))
    for path, err in result.errors[:5]:
        print(S.red(f"  ! skipped {path}: {err}"))

    if not args.no_report:
        html = write_report(out_dir, report_data(clusters, summary, plan, roots, cfg.thumb_size))
        print()
        print(f"  report  {S.cyan(str(html))}")
        if args.open:
            webbrowser.open(html.as_uri())
    print(f"  plan    {S.cyan(str(plan_path))}")
    if clusters:
        print(S.dim(f"  next:   twinlens apply \"{plan_path}\"   (dry run; add --yes to move files)"))
    return 0


def cmd_apply(args: argparse.Namespace, cfg: Config) -> int:
    _banner()
    plan_path = Path(args.plan)
    try:
        plan = read_json(plan_path, PLAN_FORMAT)
    except PlanError as exc:
        print(S.red(f"error: {exc}"), file=sys.stderr)
        return 2
    quarantine = cfg.quarantine.resolve()
    report = prepare(plan, quarantine, verify_hash=not args.no_verify)
    for path, why in report.skipped:
        print(S.yellow(f"  skip  {path}  ({why})"))
    if not report.moves:
        print("  nothing to move")
        return 0
    verb = "move" if args.yes else "would move"
    for move in report.moves[: args.top]:
        print(f"  {verb}  {move.source}")
    if len(report.moves) > args.top:
        print(S.dim(f"  … and {len(report.moves) - args.top} more"))
    print(f"  {len(report.moves)} files · {S.green(human_bytes(report.bytes))} → {S.cyan(str(quarantine))}")
    if not args.yes:
        print(S.dim("  dry run — nothing was changed. Re-run with --yes to move the files."))
        return 0
    manifest = execute(report, quarantine, plan_path.resolve())
    print(S.green(f"  ✓ moved {len(report.moves)} files"))
    print(f"  undo with: twinlens undo \"{manifest}\"")
    return 0


def _latest_manifest(quarantine: Path) -> Path | None:
    manifests = sorted(quarantine.glob("*/manifest.json"))
    return manifests[-1] if manifests else None


def cmd_undo(args: argparse.Namespace, cfg: Config) -> int:
    _banner()
    manifest = Path(args.manifest) if args.manifest else _latest_manifest(cfg.quarantine.resolve())
    if manifest is None or not manifest.is_file():
        print(S.red("error: no manifest found — pass the path printed by `twinlens apply`"), file=sys.stderr)
        return 2
    try:
        report = undo(manifest, dry_run=args.dry_run)
    except PlanError as exc:
        print(S.red(f"error: {exc}"), file=sys.stderr)
        return 2
    verb = "would restore" if args.dry_run else "restored"
    for _, dst in report.restored:
        print(f"  {verb}  {dst}")
    for dst, why in report.skipped:
        print(S.yellow(f"  skip  {dst}  ({why})"))
    skipped = S.yellow(f", {len(report.skipped)} skipped") if report.skipped else ""
    print(S.green(f"  ✓ {verb} {len(report.restored)} files") + skipped)
    return 0 if not report.skipped else 1


def cmd_demo(args: argparse.Namespace, cfg: Config) -> int:
    from .demo import generate

    _banner()
    out = Path(args.out).resolve()
    files = generate(out, scenes=args.scenes, clean=args.clean)
    print(f"  wrote {len(files)} demo photos to {S.cyan(str(out))}")
    print(S.dim(f"  try:   twinlens scan \"{out}\" --open"))
    return 0


# ── entry point ─────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="twinlens",
        description="Find near-duplicate photos, keep the best shot, clean up safely.",
    )
    p.add_argument("--version", action="version", version=f"twinlens {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="find twins and write an interactive report + plan")
    s.add_argument("paths", nargs="+", help="folders or files to scan")
    s.add_argument("-t", "--threshold", type=int, help="max pHash Hamming distance, 0-64 (default 10)")
    s.add_argument("-w", "--workers", type=int, help="hashing processes (0 = all cores)")
    s.add_argument("-o", "--out", type=Path, dest="report_dir", help="report directory")
    s.add_argument("--thumb-size", type=int, dest="thumb_size", help="thumbnail edge in pixels")
    s.add_argument("--json", metavar="FILE", help="also write machine-readable results")
    s.add_argument("--no-cache", action="store_true", help="ignore and do not update the hash cache")
    s.add_argument("--no-report", action="store_true", help="skip the HTML report")
    s.add_argument("--open", action="store_true", help="open the report in a browser")
    s.add_argument("--top", type=int, default=8, help="groups to print (default 8)")
    s.set_defaults(func=cmd_scan)

    a = sub.add_parser("apply", help="move the removals in a plan to quarantine (dry run by default)")
    a.add_argument("plan", help="plan.json from `scan` or exported from the report")
    a.add_argument("-q", "--quarantine", type=Path, help="quarantine folder")
    a.add_argument("-y", "--yes", action="store_true", help="actually move the files")
    a.add_argument("--no-verify", action="store_true", help="skip re-hashing files before moving")
    a.add_argument("--top", type=int, default=20, help="moves to list (default 20)")
    a.set_defaults(func=cmd_apply)

    u = sub.add_parser("undo", help="restore files moved by `apply`")
    u.add_argument("manifest", nargs="?", help="manifest.json (default: the latest one)")
    u.add_argument("-q", "--quarantine", type=Path, help="quarantine folder to search")
    u.add_argument("--dry-run", action="store_true", help="show what would be restored")
    u.set_defaults(func=cmd_undo)

    d = sub.add_parser("demo", help="generate a synthetic photo library to try twinlens on")
    d.add_argument("out", nargs="?", default="demo-photos", help="output folder (default demo-photos)")
    d.add_argument("--scenes", type=int, default=10, help="number of original scenes")
    d.add_argument("--clean", action="store_true", help="wipe the folder first")
    d.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    global S
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    S = Style(_enable_ansi())
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config()
        keys = ("threshold", "workers", "report_dir", "thumb_size", "quarantine")
        overrides = {k: getattr(args, k, None) for k in keys}
        cfg = cfg.with_overrides(**overrides)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
