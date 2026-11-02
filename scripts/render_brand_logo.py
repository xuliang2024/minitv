#!/usr/bin/env python3
"""Render the MiniTV brand assets (app icon, mark, wordmark, favicons).

This script is the source of truth for the MiniTV logo geometry. Everything it
writes is derived from the parameters below, so a shape tweak is a one-line
change plus a rerun.

Geometry was measured off the approved artwork kept at
``assets/brand/source/minitv-logo-approved.png`` (1024x1024). Coordinates are in
that artwork's 1024-unit space.

Usage
-----
    python3 scripts/render_brand_logo.py                # masters into assets/brand/
    python3 scripts/render_brand_logo.py --distribute   # + platform icon sets

``--distribute`` writes iOS/Android icon sets and the web favicons in place.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
BRAND = REPO / "assets" / "brand"
SOURCE = BRAND / "source" / "minitv-logo-approved.png"
WORDMARK = BRAND / "wordmark-minitv.png"

# ---------------------------------------------------------------- geometry ---
# 1024-unit space. Order matters: each vertex list must run clockwise.
SLAB = [(306, 255), (404, 255), (404, 547), (308, 547)]
SLAB_R = 30
TRI = [(412, 200), (718, 397), (412, 594)]
TRI_R = 34
HOLE = [(484, 336), (572, 397), (484, 458)]
HOLE_R = 18
GOLD = [(404, 286), (478, 397), (404, 508)]
GOLD_R = 34

# ---------------------------------------------------------------- palette ----
ROSE_FRONT_TOP = (239, 80, 101)
ROSE_FRONT_BOTTOM = (215, 30, 68)
ROSE_SIDE = (122, 27, 51)
ROSE_BACK_TOP = (206, 88, 100)
ROSE_BACK_BOTTOM = (158, 52, 74)
ROSE_BACK_SIDE = (140, 42, 62)
EDGE_LIGHT = (255, 154, 172)
GOLD_FRONT_TOP = (255, 217, 140)
GOLD_FRONT_BOTTOM = (240, 169, 60)
GOLD_SIDE = (166, 106, 20)
GOLD_EDGE = (255, 237, 192)
WORD_COLOR = (245, 242, 234)
SHADOW = (8, 1, 4)

# background ramp for the square app icon
ICON_STOPS = [(0.00, (10, 10, 16)), (0.50, (56, 14, 32)), (1.00, (104, 54, 30))]
ICON_POOL = (95, 16, 28)

# icon composition
ICON_MARK_RATIO = 0.63      # mark alpha-bbox width as a share of the icon side
ICON_MARK_ANCHOR = (0.500, 0.487)
ICON_BG_MASTER = 1024


# ----------------------------------------------------------------- helpers ---
def _lerp(c0, c1, t):
    return tuple(int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))


def rounded_path(pts, radius, scale):
    """Polygon with each corner replaced by a quadratic-bezier round."""
    pts = [(x * scale, y * scale) for x, y in pts]
    r = radius * scale
    out = []
    n = len(pts)
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        l1 = math.hypot(*v1) or 1.0
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l2 = math.hypot(*v2) or 1.0
        v1 = (v1[0] / l1, v1[1] / l1)
        v2 = (v2[0] / l2, v2[1] / l2)
        d1 = min(r, l1 / 2)
        d2 = min(r, l2 / 2)
        a = (p1[0] + v1[0] * d1, p1[1] + v1[1] * d1)
        b = (p1[0] + v2[0] * d2, p1[1] + v2[1] * d2)
        for k in range(14):
            t = k / 13
            mt = 1 - t
            out.append((mt * mt * a[0] + 2 * mt * t * p1[0] + t * t * b[0],
                        mt * mt * a[1] + 2 * mt * t * p1[1] + t * t * b[1]))
    return out


def mask_of(pts, radius, scale, size):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).polygon(rounded_path(pts, radius, scale), fill=255)
    return m


def solid(mask, color, size):
    im = Image.new("RGBA", (size, size), color + (0,))
    im.putalpha(mask)
    return im


def tint(mask, color, alpha, blur, size):
    band = mask.filter(ImageFilter.GaussianBlur(blur)) if blur else mask
    if alpha < 255:
        band = band.point(lambda v: v * alpha // 255)
    return solid(band, color, size)


def vgrad(color_top, color_bottom, y0, y1, mask, size):
    g = Image.new("RGB", (1, size))
    gp = g.load()
    for y in range(size):
        t = max(0.0, min(1.0, (y - y0) / max(1, y1 - y0)))
        gp[0, y] = _lerp(color_top, color_bottom, t)
    g = g.resize((size, size), Image.Resampling.BILINEAR).convert("RGBA")
    g.putalpha(mask)
    return g


def drop_shadow(mask, blur, dx, dy, color, alpha, size):
    """Shadow that only falls down-right, so no dark rim creeps along the top."""
    dx, dy = int(round(dx)), int(round(dy))
    sh = ImageChops.offset(mask, dx, dy).filter(ImageFilter.GaussianBlur(blur))
    keeper = ImageChops.offset(mask, max(0, int(round(blur)) - dx),
                               max(0, int(round(blur)) - dy)) \
        .filter(ImageFilter.MaxFilter(3))
    sh = ImageChops.subtract(sh, keeper)
    return tint(sh, color, alpha, 0, size)


def paste(base, im, dx, dy):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(im, (dx, dy), im)
    return Image.alpha_composite(base, layer)


# -------------------------------------------------------------------- mark ---
def render_mark(units: int) -> Image.Image:
    """The three-plane mark on transparency, drawn on a ``units``-wide canvas."""
    size = units
    s = units / 1024.0

    slab = mask_of(SLAB, SLAB_R, s, size)
    tri = mask_of(TRI, TRI_R, s, size)
    hole = mask_of(HOLE, HOLE_R, s, size)
    tri_face = ImageChops.subtract(tri, hole)
    gold = mask_of(GOLD, GOLD_R, s, size)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # back plane
    canvas = paste(canvas, drop_shadow(slab, 16 * s, 20 * s, 22 * s, SHADOW, 100, size), 0, 0)
    canvas = Image.alpha_composite(canvas, solid(ImageChops.offset(slab, int(6 * s), int(8 * s)),
                                                 ROSE_BACK_SIDE, size))
    canvas = Image.alpha_composite(canvas, vgrad(ROSE_BACK_TOP, ROSE_BACK_BOTTOM,
                                                 255 * s, 560 * s, slab, size))
    canvas = Image.alpha_composite(canvas, tint(ImageChops.subtract(slab, ImageChops.offset(slab, 0, int(10 * s))),
                                                (236, 130, 142), 110, 4 * s, size))

    # middle plane
    canvas = paste(canvas, drop_shadow(tri, 22 * s, 30 * s, 34 * s, SHADOW, 110, size), 0, 0)
    extrusion = ImageChops.subtract(ImageChops.offset(tri, int(16 * s), int(22 * s)), hole)
    canvas = Image.alpha_composite(canvas, solid(extrusion, ROSE_SIDE, size))
    canvas = Image.alpha_composite(canvas, vgrad(ROSE_FRONT_TOP, ROSE_FRONT_BOTTOM,
                                                 200 * s, 600 * s, tri_face, size))
    band = ImageChops.subtract(tri_face, ImageChops.offset(tri_face, 0, int(12 * s)))
    band = ImageChops.add(band, ImageChops.subtract(tri_face, ImageChops.offset(tri_face, int(20 * s), 0)))
    canvas = Image.alpha_composite(canvas, tint(band, EDGE_LIGHT, 80, 8 * s, size))
    ao = ImageChops.subtract(hole, ImageChops.offset(hole, int(10 * s), int(12 * s)))
    canvas = Image.alpha_composite(canvas, tint(ao, (18, 2, 8), 140, 6 * s, size))

    # front plane
    canvas = paste(canvas, drop_shadow(gold, 10 * s, 12 * s, 14 * s, (28, 8, 2), 90, size), 0, 0)
    canvas = Image.alpha_composite(canvas, solid(ImageChops.offset(gold, int(8 * s), int(10 * s)),
                                                 GOLD_SIDE, size))
    canvas = Image.alpha_composite(canvas, vgrad(GOLD_FRONT_TOP, GOLD_FRONT_BOTTOM,
                                                 280 * s, 510 * s, gold, size))
    canvas = Image.alpha_composite(canvas, tint(ImageChops.subtract(gold, ImageChops.offset(gold, 0, int(8 * s))),
                                                GOLD_EDGE, 150, 3 * s, size))
    return canvas


_MARK_CACHE: dict[int, Image.Image] = {}


def mark_units(units: int) -> Image.Image:
    if units not in _MARK_CACHE:
        _MARK_CACHE[units] = render_mark(units)
    return _MARK_CACHE[units]


def mark_sprite(units: int) -> Image.Image:
    """The mark trimmed to its visible bounds (transparency removed at edges)."""
    im = mark_units(units)
    box = im.getchannel("A").point(lambda v: 255 if v > 3 else 0).getbbox()
    return im.crop(box)


# ------------------------------------------------------------ wordmark/type ---
def wordmark_alpha() -> Image.Image:
    """Alpha of the approved MiniTV lettering, cached as a master PNG."""
    if WORDMARK.exists():
        return Image.open(WORDMARK).convert("L")
    if not SOURCE.exists():
        raise SystemExit(f"missing {SOURCE} and {WORDMARK}; cannot build the wordmark")
    src = Image.open(SOURCE).convert("RGB")
    reg = src.crop((150, 630, 890, 910)).convert("L")
    rp = reg.load()
    wa = Image.new("L", reg.size, 0)
    wp = wa.load()
    for y in range(reg.size[1]):
        for x in range(reg.size[0]):
            a = (rp[x, y] - 45) * 255 // 190
            wp[x, y] = 0 if a < 30 else min(255, a)
    box = wa.point(lambda v: 255 if v > 40 else 0).getbbox()
    wa = wa.crop(box)
    WORDMARK.parent.mkdir(parents=True, exist_ok=True)
    wa.save(WORDMARK)
    return wa


# ------------------------------------------------------------- icon canvas ---
def icon_background(px: int = ICON_BG_MASTER) -> Image.Image:
    """Opaque square brand background: ramp + rose pool + vignette + dither."""
    work = 512
    bg = Image.new("RGB", (work, work))
    bp = bg.load()
    px0, py0, prad = work * 0.51, work * 0.38, work * 0.42
    for y in range(work):
        for x in range(work):
            t = (0.30 * x + 0.92 * y) / (work * 1.22)
            r = g = b = 0
            for i in range(len(ICON_STOPS) - 1):
                t0, c0 = ICON_STOPS[i]
                t1, c1 = ICON_STOPS[i + 1]
                if t0 <= t <= t1:
                    u = (t - t0) / (t1 - t0)
                    u = u * u * (3 - 2 * u)
                    r, g, b = _lerp(c0, c1, u)
                    break
            d = math.hypot(x - px0, y - py0) / prad
            if d < 1:
                a = (1 - d) ** 2 * 0.85
                r = min(255, r + ICON_POOL[0] * a)
                g = min(255, g + ICON_POOL[1] * a)
                b = min(255, b + ICON_POOL[2] * a)
            dv = math.hypot((x - work / 2) / (work / 2), (y - work * 0.45) / (work * 0.72))
            if dv > 0.55:
                k = min(1.0, (dv - 0.55) / 0.45)
                f = 1 - 0.34 * k * k
                r *= f
                g *= f
                b *= f
            bp[x, y] = (int(r), int(g), int(b))
    bg = bg.resize((px, px), Image.Resampling.BILINEAR)
    noise = Image.frombytes("L", (px, px), os.urandom(px * px))
    bg = ImageChops.add(bg, noise.point(lambda v: v // 32).convert("RGB"), scale=1, offset=-3)
    return bg


def compose_icon(px: int) -> Image.Image:
    """Opaque square app icon: brand background with the mark centred."""
    sprite = mark_sprite(1024)
    ratio = ICON_MARK_RATIO
    target_w = max(1, int(round(px * ratio)))
    target_h = max(1, int(round(sprite.height * target_w / sprite.width)))
    sprite = sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)
    canvas = icon_background(px).convert("RGBA")
    cx = int(round(px * ICON_MARK_ANCHOR[0] - target_w / 2))
    cy = int(round(px * ICON_MARK_ANCHOR[1] - target_h / 2))
    return paste(canvas, sprite, cx, cy).convert("RGB")


def compose_foreground(px: int, ratio: float = 0.60) -> Image.Image:
    """Transparent mark for an Android adaptive-icon foreground layer."""
    sprite = mark_sprite(1024)
    target_w = max(1, int(round(px * ratio)))
    target_h = max(1, int(round(sprite.height * target_w / sprite.width)))
    sprite = sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    return paste(canvas, sprite, (px - target_w) // 2, (px - target_h) // 2)


def compose_launch(width: int, height: int) -> Image.Image:
    """Transparent launch/splash tile: the mark centred."""
    sprite = mark_sprite(1024)
    target_h = int(round(height * 0.66))
    target_w = int(round(sprite.width * target_h / sprite.height))
    sprite = sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    return paste(canvas, sprite, (width - target_w) // 2, (height - target_h) // 2)


def compose_stacked(size: int) -> Image.Image:
    """Transparent stacked lockup: mark above the MiniTV wordmark."""
    units = size // 2
    sprite = mark_sprite(units)
    mark_w = int(round(size * 0.40))
    mark_h = int(round(sprite.height * mark_w / sprite.width))
    sprite = sprite.resize((mark_w, mark_h), Image.Resampling.LANCZOS)
    wa = wordmark_alpha()
    word_w = int(round(size * 0.50))
    word_h = int(round(wa.height * word_w / wa.width))
    wa = wa.resize((word_w, word_h), Image.Resampling.LANCZOS)
    word = Image.new("RGBA", wa.size, WORD_COLOR + (255,))
    word.putalpha(wa)

    gap = int(size * 0.055)
    content_h = mark_h + gap + word_h
    top = max(0, (size - content_h) // 2)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas = paste(canvas, sprite, (size - mark_w) // 2, top)
    canvas = paste(canvas, word, (size - word_w) // 2, top + mark_h + gap)
    return canvas


# ------------------------------------------------------------- distribution ---
IOS_SIZES = [
    ("Icon-App-20x20@1x.png", 20), ("Icon-App-20x20@2x.png", 40), ("Icon-App-20x20@3x.png", 60),
    ("Icon-App-29x29@1x.png", 29), ("Icon-App-29x29@2x.png", 58), ("Icon-App-29x29@3x.png", 87),
    ("Icon-App-40x40@1x.png", 40), ("Icon-App-40x40@2x.png", 80), ("Icon-App-40x40@3x.png", 120),
    ("Icon-App-60x60@2x.png", 120), ("Icon-App-60x60@3x.png", 180),
    ("Icon-App-76x76@1x.png", 76), ("Icon-App-76x76@2x.png", 152),
    ("Icon-App-83.5x83.5@2x.png", 167), ("Icon-App-1024x1024@1x.png", 1024),
]

ANDROID_DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}

ADAPTIVE_BACKGROUND_XML = """<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android">
    <gradient
        android:angle="270"
        android:endColor="#4A1226"
        android:startColor="#0A0A10"
        android:type="linear" />
</shape>
"""

ADAPTIVE_ICON_XML = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
"""

LAUNCH_BACKGROUND_XML = """<?xml version="1.0" encoding="utf-8"?>
<layer-list xmlns:android="http://schemas.android.com/apk/res/android">
    <item android:drawable="@color/brand_launch_background" />
    <item>
        <bitmap
            android:gravity="center"
            android:src="@mipmap/launch_image" />
    </item>
</layer-list>
"""

BRAND_COLORS_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="brand_launch_background">#0B0B10</color>
</resources>
"""


def write_ios_icons() -> list[str]:
    out = []
    base = REPO / "app" / "ios" / "Runner" / "Assets.xcassets" / "AppIcon.appiconset"
    master = compose_icon(1024)
    for name, px in IOS_SIZES:
        im = master if px == 1024 else master.resize((px, px), Image.Resampling.LANCZOS)
        im.convert("RGB").save(base / name, "PNG", optimize=True)
        out.append(str(base / name))
    # launch images (transparent mark, matches LaunchScreen.storyboard 168x185)
    launch = REPO / "app" / "ios" / "Runner" / "Assets.xcassets" / "LaunchImage.imageset"
    for name, scale in (("LaunchImage.png", 1), ("LaunchImage@2x.png", 2), ("LaunchImage@3x.png", 3)):
        compose_launch(168 * scale, 185 * scale).save(launch / name, "PNG", optimize=True)
        out.append(str(launch / name))
    return out


def write_android_icons() -> list[str]:
    out = []
    res = REPO / "app" / "android" / "app" / "src" / "main" / "res"
    master = compose_icon(1024)
    for density, factor in ANDROID_DENSITIES.items():
        px = int(round(48 * factor))
        master.resize((px, px), Image.Resampling.LANCZOS) \
            .save(res / f"mipmap-{density}" / "ic_launcher.png", "PNG", optimize=True)
        out.append(str(res / f"mipmap-{density}" / "ic_launcher.png"))
        fg = int(round(108 * factor))
        compose_foreground(fg).save(res / f"mipmap-{density}" / "ic_launcher_foreground.png", "PNG")
        out.append(str(res / f"mipmap-{density}" / "ic_launcher_foreground.png"))
        splash_h = int(round(160 * factor))
        compose_launch(int(splash_h * 1.15), splash_h) \
            .save(res / f"mipmap-{density}" / "launch_image.png", "PNG")
        out.append(str(res / f"mipmap-{density}" / "launch_image.png"))

    (res / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
    for name, body in (("mipmap-anydpi-v26/ic_launcher.xml", ADAPTIVE_ICON_XML),):
        (res / name).write_text(body)
        out.append(str(res / name))
    (res / "drawable" / "ic_launcher_background.xml").write_text(ADAPTIVE_BACKGROUND_XML)
    (res / "drawable-v21" / "ic_launcher_background.xml").write_text(ADAPTIVE_BACKGROUND_XML)
    (res / "values" / "brand_colors.xml").write_text(BRAND_COLORS_XML)
    (res / "values-night" / "brand_colors.xml").write_text(BRAND_COLORS_XML)
    for name in ("drawable/launch_background.xml", "drawable-v21/launch_background.xml"):
        (res / name).write_text(LAUNCH_BACKGROUND_XML)
        out.append(str(res / name))
    return out


WEB_ASSETS = [
    ("favicon-32.png", 32),
    ("favicon-192.png", 192),
    ("apple-touch-icon.png", 180),
    ("brand/minitv-icon.png", 256),
]


def write_web_icons() -> list[str]:
    out = []
    master = compose_icon(1024)
    public = REPO / "web" / "public"
    for name, px in WEB_ASSETS:
        path = public / name
        path.parent.mkdir(parents=True, exist_ok=True)
        master.resize((px, px), Image.Resampling.LANCZOS).save(path, "PNG", optimize=True)
        out.append(str(path))
    ico = public / "favicon.ico"
    master.resize((256, 256), Image.Resampling.LANCZOS) \
        .save(ico, sizes=[(16, 16), (32, 32), (48, 48)])
    out.append(str(ico))
    return out


def write_masters() -> list[str]:
    BRAND.mkdir(parents=True, exist_ok=True)
    out = []
    compose_icon(1024).save(BRAND / "minitv-icon-1024.png", "PNG", optimize=True)
    out.append(str(BRAND / "minitv-icon-1024.png"))
    for px in (256, 512, 1024):
        mark_units(px).save(BRAND / f"minitv-mark-{px}.png", "PNG", optimize=True)
        out.append(str(BRAND / f"minitv-mark-{px}.png"))
    for px in (1024, 2048):
        compose_stacked(px).save(BRAND / f"minitv-logo-stacked-{px}.png", "PNG", optimize=True)
        out.append(str(BRAND / f"minitv-logo-stacked-{px}.png"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--distribute", action="store_true",
                    help="also write the iOS/Android icon sets and web favicons")
    args = ap.parse_args()

    written = write_masters()
    if args.distribute:
        written += write_ios_icons()
        written += write_android_icons()
        written += write_web_icons()
    for p in written:
        print("wrote", p)
    print(f"{len(written)} file(s)")


if __name__ == "__main__":
    main()
