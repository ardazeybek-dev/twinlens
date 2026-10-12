"""Render a single self-contained HTML report (thumbnails embedded, works offline)."""

from __future__ import annotations

import base64
import io
import json
from importlib import resources
from pathlib import Path

from PIL import Image

from . import __version__
from .cluster import Cluster
from .imaging import open_photo

_PLACEHOLDER = "__TWINLENS_DATA__"


def thumbnail_data_uri(path: Path, edge: int) -> str:
    try:
        img = open_photo(path)
    except Exception:
        return ""
    img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _display_dir(path: Path, roots: list[Path]) -> str:
    for root in roots:
        try:
            rel = path.parent.relative_to(root)
        except ValueError:
            continue
        return str(Path(root.name) / rel) if str(rel) != "." else root.name
    return str(path.parent)


def report_data(
    clusters: list[Cluster], summary: dict, plan: dict, roots: list[Path], thumb_edge: int
) -> dict:
    out_clusters = []
    for c in clusters:
        members = []
        for m in c.members:
            p = Path(m.record.path)
            members.append({
                "path": m.record.path,
                "name": p.name,
                "dir": _display_dir(p, roots),
                "size": m.record.size,
                "sha": m.record.sha,
                "width": m.record.width,
                "height": m.record.height,
                "sharpness": m.record.sharpness,
                "taken": m.record.taken,
                "score": m.score,
                "keep": m.keep,
                "distance": m.distance,
                "reasons": m.reasons,
                "thumb": thumbnail_data_uri(p, thumb_edge),
            })
        best = next(i for i, m in enumerate(c.members) if m.keep)
        out_clusters.append({"id": c.id, "kind": c.kind, "best": best, "members": members})
    return {"version": __version__, "summary": summary, "plan": plan, "clusters": out_clusters}


def render_html(data: dict) -> str:
    template = resources.files("twinlens").joinpath("templates/report.html").read_text(encoding="utf-8")
    # `</` inside a <script> block would end it early; JSON allows `<\/` as an escape.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace(_PLACEHOLDER, payload)


def write_report(out_dir: Path, data: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    target.write_text(render_html(data), encoding="utf-8")
    return target
