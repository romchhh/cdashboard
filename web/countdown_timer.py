"""Live countdown GIF for promo emails (Stripo-style: червоний текст на білому)."""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

COLOR_BG = (255, 255, 255)
COLOR_TEXT = (153, 0, 0)  # #990000

_LABELS = ("годин", "хвилин", "секунд")

# Логічний розмір у листі (як Stripo)
DISPLAY_W = 278
DISPLAY_H = 50
# GIF у 2× — у клієнті показуємо DISPLAY_* (чіткіше, без «пікселів»)
CANVAS_W = DISPLAY_W * 2
CANVAS_H = DISPLAY_H * 2

_COLUMN_CENTERS = (46, 139, 232)
_COLON_X = (91, 186)
_DIGIT_Y = 14
_LABEL_Y = 38
_COLON_Y = 14

_GIF_FRAMES = 60
_GIF_FRAME_MS = 1000
_GIF_COLORS = 64

_FONT_DIR = Path(__file__).resolve().parent / "static" / "fonts"
_BUNDLED_FONTS = {
    True: (
        _FONT_DIR / "DejaVuSans-Bold.ttf",
        _FONT_DIR / "Arial-Bold.ttf",
        _FONT_DIR / "LiberationSans-Bold.ttf",
    ),
    False: (
        _FONT_DIR / "DejaVuSans.ttf",
        _FONT_DIR / "Arial.ttf",
        _FONT_DIR / "LiberationSans-Regular.ttf",
    ),
}

_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _font(size: int, *, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    key = (size, bold)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached

    paths: list[Path | str] = [*_BUNDLED_FONTS[bold]]
    paths.extend(
        (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        )
    )
    for path in paths:
        try:
            font = ImageFont.truetype(str(path), size=size)
            _FONT_CACHE[key] = font
            return font
        except OSError:
            continue

    logger.warning("countdown_timer: bundled/system fonts not found, fallback to default bitmap font")
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def remaining_hms(ends_ts: int, *, now_ts: int | None = None) -> tuple[int, int, int]:
    now = int(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    left = max(0, int(ends_ts) - now)
    hours, rem = divmod(left, 3600)
    minutes, seconds = divmod(rem, 60)
    return hours, minutes, seconds


def _text_center(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    draw.text(xy, text, fill=fill, font=font, anchor="mm")


def _draw_frame(
    hours: int,
    minutes: int,
    seconds: int,
    *,
    width: int = CANVAS_W,
    height: int = CANVAS_H,
) -> Image.Image:
    scale = max(1, width // DISPLAY_W)
    w, h = DISPLAY_W * scale, DISPLAY_H * scale
    img = Image.new("RGB", (w, h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    digit_font = _font(20 * scale, bold=True)
    label_font = _font(11 * scale, bold=False)
    colon_font = _font(16 * scale, bold=True)

    values = (min(max(0, hours), 99), max(0, minutes), max(0, seconds))
    texts = tuple(f"{v:02d}" for v in values)

    col_centers = tuple(c * scale for c in _COLUMN_CENTERS)
    colon_xs = tuple(c * scale for c in _COLON_X)
    digit_y = _DIGIT_Y * scale
    label_y = _LABEL_Y * scale
    colon_y = _COLON_Y * scale

    for text, cx, label in zip(texts, col_centers, _LABELS, strict=True):
        _text_center(draw, (cx, digit_y), text, digit_font, COLOR_TEXT)
        _text_center(draw, (cx, label_y), label, label_font, COLOR_TEXT)

    for cx in colon_xs:
        _text_center(draw, (cx, colon_y), ":", colon_font, COLOR_TEXT)

    if (w, h) != (width, height):
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    return img


def _rgb_frames_to_gif(images: list[Image.Image], *, duration_ms: int) -> bytes:
    """GIF з адаптивною палітрою — зберігає згладжування тексту."""
    palette_ref = images[0].quantize(colors=_GIF_COLORS, method=Image.Quantize.MEDIANCUT)
    gif_frames = [im.quantize(palette=palette_ref, dither=Image.Dither.NONE) for im in images]

    buf = io.BytesIO()
    gif_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=gif_frames[1:] if len(gif_frames) > 1 else [],
        duration=duration_ms,
        loop=0,
        optimize=False,
    )
    return buf.getvalue()


def build_countdown_gif(
    ends_ts: int,
    *,
    frames: int = _GIF_FRAMES,
    frame_duration_ms: int = _GIF_FRAME_MS,
) -> bytes:
    base = int(datetime.now(timezone.utc).timestamp())
    n = max(1, min(int(frames), 60))
    images = [
        _draw_frame(*remaining_hms(ends_ts, now_ts=base + i), width=CANVAS_W, height=CANVAS_H)
        for i in range(n)
    ]
    return _rgb_frames_to_gif(images, duration_ms=frame_duration_ms)
