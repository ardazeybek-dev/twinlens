"""End-to-end: scan the demo library, check grouping quality and keeper choice."""

from __future__ import annotations

from pathlib import Path

from twinlens.cache import HashCache
from twinlens.cluster import build_clusters
from twinlens.imaging import analyze, laplacian_variance, open_photo
from twinlens.scanner import discover, scan


def _scene_of(files):
    return {str(f.path.resolve()): f.scene for f in files}


def test_discover_skips_tool_folders(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "twinlens-report").mkdir()
    (tmp_path / ".hidden").mkdir()
    for p in ("a/x.JPG", "twinlens-report/y.jpg", ".hidden/z.png", "notes.txt"):
        (tmp_path / p).write_bytes(b"")
    assert [p.name for p in discover([tmp_path])] == ["x.JPG"]


def test_clusters_are_pure_and_complete(demo_library):
    root, files = demo_library
    result = scan([root], HashCache(None), workers=1)
    assert not result.errors
    assert len(result.records) == len(files)

    clusters, _ = build_clusters(result.records, threshold=10)
    scene = _scene_of(files)
    for c in clusters:  # no false positives: every group holds a single scene
        assert len({scene[m.record.path] for m in c.members}) == 1

    grouped = {m.record.path for c in clusters for m in c.members}
    for f in files:
        if f.variant in ("messenger", "export", "blurry", "backup", "edited"):
            assert str(f.path.resolve()) in grouped, f"missed {f.variant}: {f.path.name}"
    singles = [f for f in files if f.variant == "unique" and f.scene != -1]
    assert not any(str(f.path.resolve()) in grouped for f in singles)


def test_keeper_is_the_camera_original(demo_library):
    root, files = demo_library
    variant = {str(f.path.resolve()): f.variant for f in files}
    clusters, _ = build_clusters(scan([root], HashCache(None)).records, threshold=10)
    for c in clusters:
        assert variant[c.keeper.record.path] in ("original", "unique"), c.keeper.record.path
        assert "Backup" not in Path(c.keeper.record.path).parent.name
        assert c.reclaimable == sum(m.record.size for m in c.members if not m.keep)


def test_exact_group_detected(demo_library):
    root, _ = demo_library
    clusters, _ = build_clusters(scan([root], HashCache(None)).records, threshold=10)
    assert any(c.kind == "exact" for c in clusters)


def test_blur_lowers_sharpness(demo_library):
    _, files = demo_library
    orig = next(f for f in files if f.variant == "original" and f.scene == 0)
    blur = next(f for f in files if f.variant == "blurry" and f.scene == 0)
    assert analyze(blur.path).sharpness < 0.5 * analyze(orig.path).sharpness


def test_sharpness_ignores_exposure(demo_library):
    _, files = demo_library
    img = open_photo(next(f for f in files if f.variant == "original").path)
    darker = img.point(lambda v: v * 0.7)
    a, b = laplacian_variance(img), laplacian_variance(darker)
    assert abs(a - b) / a < 0.1


def test_cache_hits_on_rescan(tmp_path, demo_library):
    root, files = demo_library
    db = tmp_path / "cache.sqlite"
    with HashCache(db) as cache:
        first = scan([root], cache)
    with HashCache(db) as cache:
        second = scan([root], cache)
    assert first.cache_hits == 0
    assert second.cache_hits == len(files)
    assert [r.phash for r in first.records] == [r.phash for r in second.records]


def test_corrupt_file_is_reported_not_fatal(tmp_path):
    (tmp_path / "broken.jpg").write_bytes(b"definitely not a jpeg")
    result = scan([tmp_path], HashCache(None))
    assert result.records == []
    assert result.errors and result.errors[0][0].endswith("broken.jpg")


def test_exif_orientation_is_applied(tmp_path):
    from PIL import Image

    from twinlens.demo import paint_scene
    from twinlens.hashing import from_hex, hamming

    upright = paint_scene(3, (640, 480))
    upright.save(tmp_path / "upright.jpg", quality=95)
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90° clockwise to display"
    upright.transpose(Image.Transpose.ROTATE_90).save(tmp_path / "sideways.jpg", quality=95, exif=exif)

    a, b = analyze(tmp_path / "upright.jpg"), analyze(tmp_path / "sideways.jpg")
    assert (b.width, b.height) == (640, 480)
    assert hamming(from_hex(a.phash), from_hex(b.phash)) <= 4
