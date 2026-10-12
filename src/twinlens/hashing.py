"""Perceptual hashes implemented from first principles with NumPy.

* **pHash** — 2-D DCT-II of a 32x32 grayscale thumbnail; the 8x8 lowest
  frequencies (DC term dropped) are compared against their median. Robust to
  rescaling, recompression, mild colour/brightness shifts and small crops.
* **dHash** — sign of the horizontal gradient on a 9x8 thumbnail. Cheap, and
  catches structural differences pHash can blur over, so we use it to confirm.

Both are 64-bit integers; similarity is the Hamming distance between them.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image

HASH_BITS = 64
_PHASH_SIZE = 32
_PHASH_LOW = 8
_LANCZOS = Image.Resampling.LANCZOS


@lru_cache(maxsize=4)
def dct_matrix(n: int) -> np.ndarray:
    """Orthonormal DCT-II basis: ``dct_matrix(n) @ x`` is the DCT of column vector ``x``."""
    k = np.arange(n).reshape(-1, 1)
    i = np.arange(n).reshape(1, -1)
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    m[0, :] = np.sqrt(1.0 / n)
    return m


def dct2(block: np.ndarray) -> np.ndarray:
    """Separable 2-D DCT-II: transform rows, then columns."""
    c = dct_matrix(block.shape[0])
    return c @ block @ c.T


def _bits_to_int(bits: np.ndarray) -> int:
    value = 0
    for bit in bits.ravel():
        value = (value << 1) | int(bit)
    return value


def _gray(img: Image.Image, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(img.convert("L").resize(size, _LANCZOS), dtype=np.float64)


def phash(img: Image.Image) -> int:
    pixels = _gray(img, (_PHASH_SIZE, _PHASH_SIZE))
    low = dct2(pixels)[:_PHASH_LOW, :_PHASH_LOW].ravel()
    ac = low[1:]  # the DC term only encodes mean brightness
    bits = np.concatenate(([0], ac > np.median(ac)))
    return _bits_to_int(bits)


def dhash(img: Image.Image) -> int:
    pixels = _gray(img, (9, 8))
    return _bits_to_int(pixels[:, 1:] > pixels[:, :-1])


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def to_hex(value: int) -> str:
    return f"{value:016x}"


def from_hex(text: str) -> int:
    return int(text, 16)
