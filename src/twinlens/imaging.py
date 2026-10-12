"""Opening photos and measuring them: hashes, dimensions, sharpness, capture date."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .hashing import dhash, phash, to_hex

try:  # iPhone photos are HEIC; support is optional (`pip install twinlens[heif]`).
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:  # pragma: no cover - depends on the environment
    HEIF_SUPPORT = False

Image.MAX_IMAGE_PIXELS = 300_000_000  # panoramas are legitimate; decompression bombs are not

PHOTO_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif"}
)

_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_DATETIME = 0x0132
_EXIF_IFD = 0x8769
_SHARPNESS_EDGE = 512


@dataclass(frozen=True)
class PhotoRecord:
    path: str
    size: int
    mtime: float
    sha: str
    phash: str
    dhash: str
    width: int
    height: int
    sharpness: float
    taken: str | None

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def to_dict(self) -> dict:
        return asdict(self)


def file_digest(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def _upright(raw: Image.Image) -> Image.Image:
    """Fully decoded copy with EXIF orientation applied, so rotated copies still match."""
    if getattr(raw, "is_animated", False):
        raw.seek(0)
    img = ImageOps.exif_transpose(raw)
    img.load()
    return img


def open_photo(path: Path) -> Image.Image:
    """Decode a photo into memory and release the file handle (Windows locks open files)."""
    with Image.open(path) as raw:
        return _upright(raw)


def laplacian_variance(img: Image.Image) -> float:
    """Variance of the 4-neighbour Laplacian — the classic focus/blur measure.

    The image is first normalised to a fixed longest edge so a sharp 12 MP
    original and its sharp 1 MP copy land on the same scale, and to a fixed
    contrast so exposure edits do not count as "sharper".
    """
    gray = img.convert("L")
    scale = _SHARPNESS_EDGE / max(gray.size)
    if scale < 1:
        gray = gray.resize(
            (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
            Image.Resampling.LANCZOS,
        )
    a = np.asarray(gray, dtype=np.float64)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    # Divide out global contrast: a brightened copy is not sharper than its original.
    std = a.std()
    if std < 1e-6:
        return 0.0
    a = (a - a.mean()) * (50.0 / std)
    lap = (
        a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] - 4.0 * a[1:-1, 1:-1]
    )
    return float(lap.var())


def capture_time(img: Image.Image) -> str | None:
    try:
        exif = img.getexif()
    except Exception:
        return None
    raw = exif.get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL) or exif.get(_EXIF_DATETIME)
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def analyze(path: Path) -> PhotoRecord:
    stat = path.stat()
    with Image.open(path) as raw:
        taken = capture_time(raw)
        img = _upright(raw)
    try:
        return PhotoRecord(
            path=str(path),
            size=stat.st_size,
            mtime=stat.st_mtime,
            sha=file_digest(path),
            phash=to_hex(phash(img)),
            dhash=to_hex(dhash(img)),
            width=img.width,
            height=img.height,
            sharpness=round(laplacian_variance(img), 3),
            taken=taken,
        )
    finally:
        img.close()
