"""Safety guarantees of apply/undo, the CLI, config loading and the HTML report."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from twinlens.cli import main
from twinlens.config import Config, load_config, parse_dotenv
from twinlens.demo import generate
from twinlens.plan import PLAN_FORMAT, execute, prepare, quarantine_path, undo


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    root = tmp_path / "photos"
    generate(root, scenes=3, singles=2)
    return root


def _run(capsys, *argv: str) -> tuple[int, str]:
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_scan_apply_undo_roundtrip(library, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = sorted(p.relative_to(library) for p in library.rglob("*") if p.is_file())

    code, out = _run(capsys, "scan", str(library), "--no-cache", "-w", "1")
    assert code == 0 and "twin groups" in out
    report = tmp_path / "twinlens-report"
    html = (report / "index.html").read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in html and "__TWINLENS_DATA__" not in html
    plan = json.loads((report / "plan.json").read_text(encoding="utf-8"))
    removals = [r["path"] for c in plan["clusters"] for r in c["remove"]]
    assert removals

    code, out = _run(capsys, "apply", str(report / "plan.json"))
    assert code == 0 and "dry run" in out
    assert all(Path(p).exists() for p in removals)  # dry run changed nothing

    code, out = _run(capsys, "apply", str(report / "plan.json"), "--yes")
    assert code == 0
    assert not any(Path(p).exists() for p in removals)
    for c in plan["clusters"]:  # every group still has its keeper
        assert all(Path(k).exists() for k in c["keep"])

    code, out = _run(capsys, "undo")
    assert code == 0 and f"restored {len(removals)} files" in out
    after = sorted(p.relative_to(library) for p in library.rglob("*") if p.is_file())
    assert after == before


def test_apply_refuses_changed_files(library, tmp_path):
    victim = next(library.rglob("*.png"))
    keeper = next(library.rglob("*.jpg"))
    plan = {"format": PLAN_FORMAT, "clusters": [{
        "id": 1, "keep": [str(keeper)],
        "remove": [{"path": str(victim), "size": victim.stat().st_size, "sha": "0" * 32}],
    }]}
    report = prepare(plan, tmp_path / "q")
    assert not report.moves
    assert "changed since the scan" in report.skipped[0][1]


def test_apply_never_removes_the_last_copy(library, tmp_path):
    a, b = sorted(library.rglob("*.jpg"))[:2]
    plan = {"format": PLAN_FORMAT, "clusters": [{
        "id": 1, "keep": [],
        "remove": [{"path": str(p), "size": p.stat().st_size, "sha": ""} for p in (a, b)],
    }]}
    report = prepare(plan, tmp_path / "q")
    assert not report.moves and len(report.skipped) == 2


def test_undo_does_not_overwrite(library, tmp_path):
    keeper, victim = sorted(library.rglob("*.jpg"))[:2]
    plan = {"format": PLAN_FORMAT, "clusters": [{
        "id": 1, "keep": [str(keeper)],
        "remove": [{"path": str(victim), "size": victim.stat().st_size, "sha": ""}],
    }]}
    manifest = execute(prepare(plan, tmp_path / "q"), tmp_path / "q")
    shutil.copy2(keeper, victim)  # someone put a new file at the original path
    result = undo(manifest)
    assert not result.restored and result.skipped


def test_quarantine_path_mirrors_absolute_paths(tmp_path):
    src = (tmp_path / "Pictures" / "a.jpg").resolve()
    target = quarantine_path(tmp_path / "q", src)
    assert target.name == "a.jpg" and target.parent.name == "Pictures"
    assert str(target).startswith(str(tmp_path / "q"))


def test_parse_dotenv():
    text = """
    # comment
    TWINLENS_THRESHOLD=12
    export TWINLENS_WORKERS = 3
    TWINLENS_REPORT_DIR="my report"
    TWINLENS_CACHE=c.sqlite # trailing comment
    broken line
    """
    assert parse_dotenv(text) == {
        "TWINLENS_THRESHOLD": "12",
        "TWINLENS_WORKERS": "3",
        "TWINLENS_REPORT_DIR": "my report",
        "TWINLENS_CACHE": "c.sqlite",
    }


def test_config_precedence(tmp_path):
    (tmp_path / ".env").write_text("TWINLENS_THRESHOLD=12\nTWINLENS_THUMB_SIZE=200\n", encoding="utf-8")
    cfg = load_config(tmp_path, environ={"TWINLENS_THRESHOLD": "7"})
    assert cfg.threshold == 7 and cfg.thumb_size == 200
    assert cfg.with_overrides(threshold=3, workers=None).threshold == 3


def test_config_validation(tmp_path):
    with pytest.raises(ValueError):
        Config(threshold=99)
    with pytest.raises(ValueError, match="TWINLENS_WORKERS"):
        load_config(tmp_path, environ={"TWINLENS_WORKERS": "many"})


def test_report_escapes_script_breakers(tmp_path):
    from twinlens.report import render_html

    html = render_html({"version": "x", "summary": {}, "plan": {}, "clusters": [], "evil": "</script><b>"})
    assert "</script><b>" not in html
