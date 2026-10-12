from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageEnhance

from twinlens.demo import paint_scene
from twinlens.hashing import dct2, dct_matrix, dhash, from_hex, hamming, phash, to_hex


def test_dct_matrix_is_orthonormal():
    c = dct_matrix(32)
    assert np.allclose(c @ c.T, np.eye(32), atol=1e-10)


def test_dct2_of_constant_block_has_only_dc():
    out = dct2(np.full((8, 8), 7.0))
    assert out[0, 0] == pytest.approx(56.0)
    out[0, 0] = 0
    assert np.abs(out).max() < 1e-9


def test_hex_roundtrip_and_hamming():
    for value in (0, 1, 0xDEADBEEF, (1 << 64) - 1):
        assert from_hex(to_hex(value)) == value
    assert hamming(0b1011, 0b0001) == 2
    assert hamming((1 << 64) - 1, 0) == 64


@pytest.fixture(scope="module")
def scene() -> Image.Image:
    return paint_scene(7, size=(800, 600))


@pytest.mark.parametrize("fn", [phash, dhash])
def test_hashes_survive_resize_and_recompression(scene, fn, tmp_path):
    small = scene.resize((400, 300), Image.Resampling.LANCZOS)
    path = tmp_path / "small.jpg"
    small.save(path, quality=50)
    with Image.open(path) as recompressed:
        assert hamming(fn(scene), fn(recompressed)) <= 10


def test_phash_ignores_brightness(scene):
    brighter = ImageEnhance.Brightness(scene).enhance(1.15)
    assert hamming(phash(scene), phash(brighter)) <= 6


def test_different_scenes_are_far_apart():
    a, b = paint_scene(1, (640, 480)), paint_scene(2, (640, 480))
    assert hamming(phash(a), phash(b)) > 12 or hamming(dhash(a), dhash(b)) > 18


def test_hash_is_64_bits(scene):
    assert 0 <= phash(scene) < 1 << 64
    assert 0 <= dhash(scene) < 1 << 64
