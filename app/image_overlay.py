"""Pillow-based text overlay renderer for patch background images.

Renders "Book Title - Patch Name" onto a background image and saves it as PNG.
The resulting image is used as-is for video generation (no drawtext in FFmpeg).
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.models import Book, Patch

logger = logging.getLogger(__name__)

_FONT_SIZE = 52
_TEXT_COLOR = (255, 255, 255)
_SHADOW_COLOR = (0, 0, 0)
_SHADOW_OFFSET = 3
_PADDING_TOP = 50

_VIETNAMESE_TEST = "âêôưỢĐáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ"

# Font fallback chain: user-configured → known-good system fonts → built-in default
_FONT_PATHS: list[str] = [
    # Segoe UI (Windows) — excellent Vietnamese support
    "C:/Windows/Fonts/SEGOEUI.TTF",
    "C:/Windows/Fonts/segoeui.ttf",
    # Arial (Windows) — precomposed Vietnamese glyphs
    "C:/Windows/Fonts/ARIAL.TTF",
    "C:/Windows/Fonts/arial.ttf",
    # Times New Roman
    "C:/Windows/Fonts/TIMES.TTF",
    "C:/Windows/Fonts/times.ttf",
    # Tahoma
    "C:/Windows/Fonts/TAHOMA.TTF",
    "C:/Windows/Fonts/tahoma.ttf",
    # Bundled Noto Sans Vietnamese (if user downloaded manually)
    str(Path(__file__).parent.parent / "assets" / "fonts" / "NotoSansVietnamese.ttf"),
    str(Path(__file__).parent.parent / "assets" / "fonts" / "NotoSansVietnamese-Regular.ttf"),
]


def get_patch_overlay_path(book_id: int, patch_id: int) -> Path:
    return Path(settings.data_root) / "books" / str(book_id) / "patch_overlays" / f"{patch_id}.png"


def _resolve_background(book: Book) -> Path | None:
    if book.background_image_path and Path(book.background_image_path).exists():
        return Path(book.background_image_path)
    default = Path(settings.default_background_image)
    if default.exists():
        return default
    return None


def _validate_vietnamese(font, test_chars: str = _VIETNAMESE_TEST) -> bool:
    """Check whether the font can render common Vietnamese characters (not boxes)."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(img)
        for ch in test_chars:
            bbox = draw.textbbox((0, 0), ch, font=font)
            w = bbox[2] - bbox[0]
            if w == 0:
                return False
        return True
    except Exception:
        return False


def _try_load_font(path: str, size: int):
    from PIL import ImageFont
    try:
        font = ImageFont.truetype(path, size)
        if _validate_vietnamese(font):
            return font
        logger.warning("image_overlay: font at %s loaded but missing Vietnamese glyphs", path)
    except Exception as exc:
        logger.debug("image_overlay: cannot load font %s: %s", path, exc)
    return None


def _load_font(font_path: str | None, size: int):
    from PIL import ImageFont

    # 1. Try user-configured font
    if font_path:
        font = _try_load_font(font_path, size)
        if font:
            return font
        logger.warning("image_overlay: configured font %s missing Vietnamese glyphs, trying fallbacks", font_path)

    # 2. Try fallback chain (system fonts + bundled)
    for fp in _FONT_PATHS:
        font = _try_load_font(fp, size)
        if font:
            logger.info("image_overlay: using fallback font %s", fp)
            return font

    # 3. Last resort — built-in bitmap (likely won't render Vietnamese either)
    logger.error("image_overlay: no usable font found — Vietnamese characters may render as boxes")
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def needs_rerender(book: Book, patch: Patch, out_path: Path) -> bool:
    if not out_path.exists():
        return True
    bg = _resolve_background(book)
    if bg is None:
        return False
    try:
        return bg.stat().st_mtime > out_path.stat().st_mtime
    except OSError:
        return True


def render_patch_overlay(book: Book, patch: Patch, font_path: str | None, out_path: str) -> None:
    from PIL import Image, ImageDraw

    bg = _resolve_background(book)
    if bg is None:
        raise ValueError(f"no background image available for book {book.id}")

    img = Image.open(str(bg)).convert("RGB")
    draw = ImageDraw.Draw(img)
    width, height = img.size

    patch_label = patch.name or str(patch.patch_index)
    text = f"{book.title} - {patch_label}"

    font = _load_font(font_path, _FONT_SIZE)

    try:
        text_width = draw.textlength(text, font=font)
    except AttributeError:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]

    if text_width > width - 40:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            try:
                tw = draw.textlength(test, font=font)
            except AttributeError:
                bbox = draw.textbbox((0, 0), test, font=font)
                tw = bbox[2] - bbox[0]
            if tw <= width - 40:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    else:
        lines = [text]

    try:
        line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + 8
    except AttributeError:
        line_height = _FONT_SIZE + 8

    y = _PADDING_TOP
    for line in lines:
        try:
            lw = draw.textlength(line, font=font)
        except AttributeError:
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
        x = (width - lw) / 2

        for dx in range(-_SHADOW_OFFSET, _SHADOW_OFFSET + 1):
            for dy in range(-_SHADOW_OFFSET, _SHADOW_OFFSET + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), line, font=font, fill=_SHADOW_COLOR)

        draw.text((x, y), line, font=font, fill=_TEXT_COLOR)
        y += line_height

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), "PNG")


def ensure_patch_overlay(book: Book, patch: Patch, font_path: str | None = None) -> str | None:
    if _resolve_background(book) is None:
        return None
    out_path = get_patch_overlay_path(book.id, patch.id)
    if needs_rerender(book, patch, out_path):
        try:
            render_patch_overlay(book, patch, font_path, str(out_path))
        except Exception as exc:
            logger.error("image_overlay: failed to render overlay for patch %s: %s", patch.id, exc)
            return None
    return str(out_path)
