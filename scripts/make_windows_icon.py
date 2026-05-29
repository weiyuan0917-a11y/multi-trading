from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SVG = ROOT / "frontend" / "public" / "brand" / "multitrading-logo.svg"
OUT_DIR = ROOT / "assets" / "windows"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _gradient(size: tuple[int, int], colors: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]) -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    pixels = img.load()
    w, h = size
    for y in range(h):
        for x in range(w):
            tx = x / max(1, w - 1)
            ty = y / max(1, h - 1)
            w1 = max(0.0, 1.0 - (tx * 0.75 + ty * 0.7))
            w2 = max(0.0, 1.0 - abs((tx + ty) / 2.0 - 0.52) * 1.35)
            w3 = max(0.0, tx * 0.65 + ty * 0.9)
            total = max(0.001, w1 + w2 + w3)
            r = int((colors[0][0] * w1 + colors[1][0] * w2 + colors[2][0] * w3) / total)
            g = int((colors[0][1] * w1 + colors[1][1] * w2 + colors[2][1] * w3) / total)
            b = int((colors[0][2] * w1 + colors[1][2] * w2 + colors[2][2] * w3) / total)
            pixels[x, y] = (r, g, b, 255)
    return img


def _draw_mark(draw: ImageDraw.ImageDraw, layer: Image.Image, s: float, ox: int, oy: int) -> None:
    def p(x: float, y: float) -> tuple[int, int]:
        return int(ox + x * s), int(oy + y * s)

    bg = _gradient((int(56 * s), int(56 * s)), ((18, 36, 63), (11, 23, 42), (10, 44, 50)))
    mask = _rounded_mask(bg.size, int(15 * s))
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(max(1, int(1.4 * s)))))
    layer.alpha_composite(shadow, p(4, 5))
    layer.alpha_composite(Image.composite(bg, Image.new("RGBA", bg.size, (0, 0, 0, 0)), mask), p(4, 4))

    ring_width = max(2, int(2 * s))
    draw.rounded_rectangle((*p(5, 5), *p(59, 59)), radius=int(14 * s), outline=(102, 231, 255, 255), width=ring_width)
    draw.rounded_rectangle((*p(6.5, 6.5), *p(57.5, 57.5)), radius=int(13 * s), outline=(91, 108, 255, 190), width=max(1, ring_width // 2))
    draw.rounded_rectangle((*p(8, 8), *p(56, 56)), radius=int(12 * s), outline=(32, 242, 169, 150), width=max(1, ring_width // 2))

    grid = (148, 163, 184, 108)
    gw = max(1, int(2 * s))
    for x, ytop in [(18, 22), (28, 17), (38, 27), (48, 15)]:
        draw.line((*p(x, ytop), *p(x, 46)), fill=grid, width=gw)
    draw.line((*p(15, 46), *p(51, 46)), fill=grid, width=gw)

    tick = (216, 252, 255, 235)
    tw = max(1, int(2.4 * s))
    for x1, y in [(15, 30), (25, 25), (35, 34), (45, 23)]:
        draw.line((*p(x1, y), *p(x1 + 6, y)), fill=tick, width=tw)

    points = [p(15, 39), p(24.5, 31), p(32.5, 35), p(47, 22)]
    glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.line(points, fill=(63, 219, 255, 130), width=max(4, int(6.6 * s)), joint="curve")
    layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(1, int(1.2 * s)))))
    draw.line(points, fill=(125, 211, 252, 255), width=max(2, int(4.2 * s)), joint="curve")
    draw.line(points[1:], fill=(91, 108, 255, 255), width=max(2, int(3.7 * s)), joint="curve")
    draw.line(points[2:], fill=(52, 211, 153, 255), width=max(2, int(3.2 * s)), joint="curve")
    draw.line([p(47, 22), p(48.4, 29.3), p(41.2, 26.9)], fill=(138, 251, 226, 255), width=max(2, int(3.2 * s)), joint="curve")
    for x, y in [(24.5, 31), (32.5, 35)]:
        cx, cy = p(x, y)
        r = max(2, int(2.3 * s))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(216, 252, 255, 255))


def _draw_full_logo(width: int = 1856, height: int = 512) -> Image.Image:
    # This mirrors frontend/public/brand/multitrading-logo.svg for Windows icon use.
    s = height / 64
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_mark(draw, img, s, 0, 0)

    font = _font(int(24 * s))
    text = "MultiTrading"
    text_x = int(74 * s)
    text_y = int(15 * s)
    mask = Image.new("L", img.size, 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.text((text_x, text_y), text, font=font, fill=255)
    text_gradient = _gradient(img.size, ((248, 250, 252), (216, 252, 255), (138, 251, 226)))
    img.alpha_composite(Image.composite(text_gradient, Image.new("RGBA", img.size, (0, 0, 0, 0)), mask))

    underline_y = int(47 * s)
    draw.line((int(75 * s), underline_y, int(206 * s), underline_y), fill=(102, 231, 255, 150), width=max(2, int(2 * s)))
    return img


def main() -> None:
    if not SOURCE_SVG.exists():
        raise FileNotFoundError(SOURCE_SVG)
    png = OUT_DIR / "multitrading-logo-icon.png"
    ico = OUT_DIR / "multitrading-logo.ico"
    wide = _draw_full_logo()
    square = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    fitted = wide.resize((928, 256), Image.Resampling.LANCZOS)
    square.alpha_composite(fitted, ((1024 - fitted.width) // 2, (1024 - fitted.height) // 2))
    square.save(png)
    square.save(
        ico,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"source {SOURCE_SVG}")
    print(f"created {png}")
    print(f"created {ico}")


if __name__ == "__main__":
    main()
