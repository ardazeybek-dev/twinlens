"""Generate a deterministic, license-free "camera roll" full of realistic duplicate traps.

Every scene is painted procedurally (sky gradient, sun, layered mountains, lake,
grain), then saved alongside the copies real libraries accumulate: messenger
recompressions, resized exports, crops, brightness edits, a blurry burst frame
and a byte-identical backup.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

_PALETTES = [
    ((255, 170, 110), (90, 60, 140)),   # dusk
    ((140, 200, 255), (30, 90, 170)),   # noon
    ((255, 220, 150), (240, 110, 90)),  # golden hour
    ((60, 70, 120), (10, 15, 40)),      # night
    ((200, 240, 230), (70, 150, 160)),  # coast
    ((250, 200, 210), (120, 90, 160)),  # spring
    ((235, 235, 220), (140, 150, 160)), # overcast
    ((255, 150, 80), (60, 20, 60)),     # wildfire sky
]


@dataclass(frozen=True)
class DemoFile:
    path: Path
    variant: str
    scene: int


def paint_scene(seed: int, size: tuple[int, int] = (1600, 1200)) -> Image.Image:
    rng = np.random.default_rng(seed)
    w, h = size
    base_top, base_bottom = _PALETTES[seed % len(_PALETTES)]
    tint = rng.integers(-40, 41, 3)
    top = np.clip(np.array(base_top) + tint, 0, 255)
    bottom = np.clip(np.array(base_bottom) + tint[::-1], 0, 255)
    t = np.linspace(0.0, 1.0, h).reshape(-1, 1, 1)
    sky = (top * (1 - t) + bottom * t).repeat(w, axis=1)
    # light falls off sideways from wherever the sun sits, like a real exposure
    sx, sy = rng.uniform(0.1, 0.9) * w, rng.uniform(0.1, 0.4) * h
    xs = np.arange(w).reshape(1, -1, 1)
    sky = sky * (0.75 + 0.35 * np.exp(-(((xs - sx) / (0.45 * w)) ** 2)))
    img = Image.fromarray(np.clip(sky, 0, 255).astype(np.uint8), "RGB")

    clouds = Image.new("L", size, 0)
    cdraw = ImageDraw.Draw(clouds)
    for _ in range(int(rng.integers(3, 10))):
        cx, cy = rng.uniform(0, w), rng.uniform(0, 0.45) * h
        rw, rh = rng.uniform(0.06, 0.2) * w, rng.uniform(0.02, 0.06) * h
        cdraw.ellipse((cx - rw, cy - rh, cx + rw, cy + rh), fill=int(rng.integers(90, 200)))
    img.paste((250, 250, 255), mask=clouds.filter(ImageFilter.GaussianBlur(18)))

    draw = ImageDraw.Draw(img)
    r = rng.uniform(0.04, 0.09) * w
    draw.ellipse((sx - r, sy - r, sx + r, sy + r), fill=tuple(int(c) for c in rng.integers(200, 256, 3)))

    for layer in range(int(rng.integers(2, 5))):
        base = h * (0.42 + 0.13 * layer)
        px = np.sort(np.concatenate(([0, w], rng.uniform(0, w, int(rng.integers(4, 12))))))
        py = base - rng.uniform(0.02, 0.32, px.size) * h * (1 - 0.15 * layer)
        shade = np.clip(bottom * (0.3 + 0.17 * layer) + rng.integers(-35, 35, 3), 0, 255)
        draw.polygon([(0, h), *zip(px.tolist(), py.tolist(), strict=True), (w, h)], fill=tuple(int(c) for c in shade))

    if rng.random() < 0.6:
        lake = h * rng.uniform(0.72, 0.85)
        draw.rectangle((0, lake, w, h), fill=tuple(int(c * 0.7) for c in top))

    for _ in range(int(rng.integers(6, 18))):  # trees / rocks give the frame real structure
        x, y = rng.uniform(0, w), rng.uniform(0.55, 0.97) * h
        s = rng.uniform(0.015, 0.06) * w
        green = (int(rng.integers(15, 50)), int(rng.integers(40, 90)), int(rng.integers(20, 50)))
        draw.polygon([(x, y - 2.4 * s), (x - s, y), (x + s, y)], fill=green)

    grain = rng.normal(0, 6, (h, w, 3))
    arr = np.clip(np.asarray(img, dtype=np.float64) + grain, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB").filter(ImageFilter.GaussianBlur(0.6))


def camera_exif(scene: int, burst: int = 0) -> Image.Exif:
    """EXIF a phone camera would write; messenger apps strip it, which helps pick the original."""
    exif = Image.Exif()
    stamp = f"2026:07:{1 + scene % 28:02d} 18:{10 + scene % 50:02d}:{burst:02d}"
    exif[0x010F] = "twinlens demo camera"  # Make
    exif[0x0132] = stamp  # DateTime
    exif.get_ifd(0x8769)[0x9003] = stamp  # DateTimeOriginal
    return exif


def _crop(img: Image.Image, frac: float) -> Image.Image:
    dx, dy = int(img.width * frac), int(img.height * frac)
    return img.crop((dx, dy, img.width - dx, img.height - dy))


def generate(out: Path, scenes: int = 10, singles: int = 8, clean: bool = False) -> list[DemoFile]:
    """Write a demo library to ``out`` and return what was written (for tests and docs)."""
    if clean and out.exists():
        shutil.rmtree(out)
    camera, chats, exports, backup = (out / d for d in ("Camera", "WhatsApp Images", "Exports", "Backup 2024"))
    for d in (camera, chats, exports, backup):
        d.mkdir(parents=True, exist_ok=True)

    written: list[DemoFile] = []

    def save(img: Image.Image, path: Path, variant: str, scene: int, **kw: object) -> None:
        img.save(path, **kw)
        written.append(DemoFile(path, variant, scene))

    for s in range(scenes):
        original = paint_scene(1000 + s)
        stem = f"IMG_{2041 + s:04d}"
        save(original, camera / f"{stem}.jpg", "original", s, quality=95, exif=camera_exif(s))
        save(original.resize((800, 600), Image.Resampling.LANCZOS),
             chats / f"IMG-2026{s:02d}14-WA00{s:02d}.jpg", "messenger", s, quality=55)
        if s % 2 == 0:
            save(original.resize((1200, 900), Image.Resampling.LANCZOS),
                 exports / f"{stem}-export.png", "export", s)
        if s % 3 == 0:
            save(_crop(original, 0.04), exports / f"{stem}-crop.jpg", "crop", s, quality=90)
        if s % 3 == 1:
            save(ImageEnhance.Brightness(original).enhance(1.12),
                 exports / f"{stem}-edited.jpg", "edited", s, quality=92)
        if s % 4 == 0:
            save(original.filter(ImageFilter.GaussianBlur(3)),
                 camera / f"{stem}_BURST02.jpg", "blurry", s, quality=95, exif=camera_exif(s, 1))
        if s % 5 == 0:
            target = backup / f"{stem}.jpg"
            shutil.copy2(camera / f"{stem}.jpg", target)
            written.append(DemoFile(target, "backup", s))

    for k in range(singles):
        save(paint_scene(5000 + k), camera / f"IMG_{3100 + k:04d}.jpg", "unique", -1 - k,
             quality=92, exif=camera_exif(40 + k))
    if singles:  # a byte-identical backup of an otherwise unique shot -> an "exact" group
        target = backup / "IMG_3100 (1).jpg"
        shutil.copy2(camera / "IMG_3100.jpg", target)
        written.append(DemoFile(target, "backup", -1))
    return written
