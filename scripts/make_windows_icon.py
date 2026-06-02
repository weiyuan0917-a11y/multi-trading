from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "windows"
SOURCE_PNG = OUT_DIR / "multitrading-logo-source.png"
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
CANVAS_SIZE = 1024
SOURCE_BBOX_PADDING = 0
LEGACY_PADDING = 28


def resolve_source() -> Path:
    if SOURCE_PNG.exists():
        return SOURCE_PNG
    raise FileNotFoundError(f"missing icon source: {SOURCE_PNG}")


def load_source_mark(source: Path) -> Image.Image:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")

    bbox = image.getbbox()
    if not bbox:
        raise ValueError(f"icon source is empty: {source}")
    left, top, right, bottom = bbox
    left = max(0, left - SOURCE_BBOX_PADDING)
    top = max(0, top - SOURCE_BBOX_PADDING)
    right = min(image.width, right + SOURCE_BBOX_PADDING)
    bottom = min(image.height, bottom + SOURCE_BBOX_PADDING)
    return image.crop((left, top, right, bottom))


def fit_on_square(image: Image.Image, target_size: int) -> Image.Image:
    ratio = target_size / max(image.width, image.height)
    fitted_size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    fitted = image.resize(fitted_size, Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    x = (CANVAS_SIZE - fitted.width) // 2
    y = (CANVAS_SIZE - fitted.height) // 2
    square.alpha_composite(fitted, (x, y))
    return square


def make_launcher_icon(source: Path) -> Image.Image:
    mark = load_source_mark(source)
    return fit_on_square(mark, 960)


def color(hex_value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = hex_value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4)) + (alpha,)


def draw_download_badge(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0
    badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)

    pad = max(8, int(width * 0.045))
    radius = int(width * 0.22)

    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((pad, pad, width - pad, height - pad), radius=radius, fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, int(width * 0.025))))
    badge.alpha_composite(shadow)

    draw.rounded_rectangle(
        (pad, pad, width - pad, height - pad),
        radius=radius,
        fill=color("#082334", 232),
        outline=color("#35e0c8"),
        width=max(3, int(width * 0.035)),
    )
    draw.rounded_rectangle(
        (pad * 2, pad * 2, width - pad * 2, height - pad * 2),
        radius=max(1, radius - pad),
        outline=color("#7ef6e7", 115),
        width=max(1, int(width * 0.012)),
    )

    center_x = width // 2
    top = int(height * 0.18)
    shaft_bottom = int(height * 0.58)
    shaft_width = int(width * 0.14)
    head_y = int(height * 0.51)
    head_width = int(width * 0.56)
    head_bottom = int(height * 0.80)
    line_y = int(height * 0.86)

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.rounded_rectangle(
        (center_x - shaft_width // 2, top, center_x + shaft_width // 2, shaft_bottom),
        radius=max(1, shaft_width // 2),
        fill=(82, 232, 194, 130),
    )
    glow_draw.polygon(
        [(center_x - head_width // 2, head_y), (center_x + head_width // 2, head_y), (center_x, head_bottom)],
        fill=(82, 232, 194, 130),
    )
    glow_draw.rounded_rectangle(
        (int(width * 0.20), line_y - int(height * 0.027), int(width * 0.80), line_y + int(height * 0.027)),
        radius=max(1, int(height * 0.027)),
        fill=(82, 232, 194, 125),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, int(width * 0.025))))
    badge.alpha_composite(glow)

    arrow_color = color("#8ff4d8")
    line_color = color("#4be2bd")
    draw.rounded_rectangle(
        (center_x - shaft_width // 2, top, center_x + shaft_width // 2, shaft_bottom),
        radius=max(1, shaft_width // 2),
        fill=arrow_color,
    )
    draw.polygon(
        [(center_x - head_width // 2, head_y), (center_x + head_width // 2, head_y), (center_x, head_bottom)],
        fill=arrow_color,
    )
    draw.rounded_rectangle(
        (int(width * 0.20), line_y - int(height * 0.027), int(width * 0.80), line_y + int(height * 0.027)),
        radius=max(1, int(height * 0.027)),
        fill=line_color,
    )

    canvas.alpha_composite(badge, (x0, y0))


def make_installer_icon(source: Path) -> Image.Image:
    mark = load_source_mark(source)
    square = fit_on_square(mark, 880)
    mark_box = square.getbbox()
    if not mark_box:
        return square

    left, top, right, bottom = mark_box
    mark_size = min(right - left, bottom - top)
    badge_size = int(mark_size * 0.31)
    inset_right = int(mark_size * 0.105)
    inset_bottom = int(mark_size * 0.105)
    x1 = right - inset_right - int(mark_size * 0.015)
    y1 = bottom - inset_bottom - int(mark_size * 0.012)
    draw_download_badge(square, (x1 - badge_size, y1 - badge_size, x1, y1))
    return square


def make_legacy_icon(source: Path) -> Image.Image:
    mark = load_source_mark(source)
    return fit_on_square(mark, CANVAS_SIZE - LEGACY_PADDING * 2)


def save_icon(image: Image.Image, png_path: Path, ico_path: Path) -> None:
    image.save(png_path)
    image.save(ico_path, format="ICO", sizes=ICON_SIZES)
    print(f"created {png_path}")
    print(f"created {ico_path}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = resolve_source()
    print(f"source {source}")
    save_icon(make_launcher_icon(source), OUT_DIR / "multitrading-launcher-icon.png", OUT_DIR / "multitrading-launcher.ico")
    save_icon(make_installer_icon(source), OUT_DIR / "multitrading-installer-icon.png", OUT_DIR / "multitrading-installer.ico")
    save_icon(make_legacy_icon(source), OUT_DIR / "multitrading-logo-icon.png", OUT_DIR / "multitrading-logo.ico")


if __name__ == "__main__":
    main()
