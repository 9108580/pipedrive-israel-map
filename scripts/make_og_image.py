"""Generate map/og-map.png (1200x630) for Facebook/Open Graph previews."""
from __future__ import annotations

import io
import json
import math
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "map" / "data" / "projects.geojson"
OUT = ROOT / "map" / "og-map.png"
LOGO = ROOT / "map" / "logo.png"

W, H = 1200, 630
WEST, SOUTH, EAST, NORTH = 34.2, 29.45, 35.95, 33.35
ZOOM = 8


def latlon_to_tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def fetch_tile(z: int, x: int, y: int, cache: dict) -> Image.Image:
    key = (z, x, y)
    if key in cache:
        return cache[key]
    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    req = urllib.request.Request(
        url, headers={"User-Agent": "pipedrive-israel-map-og/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        print("tile fail", z, x, y, exc)
        img = Image.new("RGB", (256, 256), (230, 235, 240))
    cache[key] = img
    time.sleep(0.12)
    return img


def main() -> None:
    x0f, y1f = latlon_to_tile(NORTH, WEST, ZOOM)
    x1f, y0f = latlon_to_tile(SOUTH, EAST, ZOOM)
    tx0, tx1 = int(math.floor(x0f)), int(math.floor(x1f))
    ty0, ty1 = int(math.floor(y1f)), int(math.floor(y0f))
    print("tiles", tx0, ty0, "->", tx1, ty1, "count", (tx1 - tx0 + 1) * (ty1 - ty0 + 1))

    cache: dict = {}
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (200, 210, 220))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            mosaic.paste(fetch_tile(ZOOM, tx, ty, cache), ((tx - tx0) * 256, (ty - ty0) * 256))
        print("row", ty)

    left = (x0f - tx0) * 256
    right = (x1f - tx0) * 256
    top = (y1f - ty0) * 256
    bottom = (y0f - ty0) * 256
    cropped = mosaic.crop((int(left), int(top), int(right), int(bottom)))

    cw, ch = cropped.size
    scale = max(W / cw, H / ch)
    nw, nh = int(cw * scale), int(ch * scale)
    resized = cropped.resize((nw, nh), Image.Resampling.LANCZOS)
    ox = (nw - W) // 2
    oy = (nh - H) // 2
    base = resized.crop((ox, oy, ox + W, oy + H))
    base = ImageEnhance.Color(base).enhance(0.95)
    base = ImageEnhance.Contrast(base).enhance(1.05)

    def project(lon: float, lat: float) -> tuple[float, float]:
        xf, yf = latlon_to_tile(lat, lon, ZOOM)
        px = (xf - x0f) / (x1f - x0f) * cw
        py = (yf - y1f) / (y0f - y1f) * ch
        return px * scale - ox, py * scale - oy

    features = json.loads(GEO.read_text(encoding="utf-8")).get("features") or []
    print("features", len(features))

    draw = ImageDraw.Draw(base)
    for f in features:
        try:
            lon, lat = f["geometry"]["coordinates"][:2]
        except Exception:  # noqa: BLE001
            continue
        x, y = project(lon, lat)
        if x < -10 or y < -10 or x > W + 10 or y > H + 10:
            continue
        r = 4
        draw.ellipse((x - r - 1, y - r - 1, x + r + 1, y + r + 1), fill=(30, 111, 217))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255))
        draw.ellipse((x - r + 1.5, y - r + 1.5, x + r - 1.5, y + r - 1.5), fill=(30, 111, 217))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(110):
        a = int(170 * (1 - i / 110))
        od.line([(0, i), (W, i)], fill=(14, 22, 36, a))
    for i in range(90):
        a = int(140 * (i / 90))
        y = H - 90 + i
        od.line([(0, y), (W, y)], fill=(14, 22, 36, a))

    base = Image.alpha_composite(base.convert("RGBA"), overlay)

    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        logo.thumbnail((96, 96), Image.Resampling.LANCZOS)
        mask = Image.new("L", logo.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, logo.size[0] - 1, logo.size[1] - 1), fill=255)
        circ = Image.new("RGBA", logo.size, (255, 255, 255, 0))
        circ.paste(logo, (0, 0), mask)
        pad = 6
        badge = Image.new("RGBA", (circ.size[0] + pad * 2, circ.size[1] + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(badge).ellipse((0, 0, badge.size[0] - 1, badge.size[1] - 1), fill=(255, 255, 255, 230))
        badge.paste(circ, (pad, pad), circ)
        base.paste(badge, (36, 28), badge)

    try:
        font_big = ImageFont.truetype("arial.ttf", 42)
        font_sm = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font_big = ImageFont.load_default()
        font_sm = font_big

    td = ImageDraw.Draw(base)
    td.text((160, 36), "Solar Projects — Israel", font=font_big, fill=(255, 255, 255, 255))
    td.text(
        (160, 88),
        f"{len(features)} installations  ·  מומחי אנרגיה סולארית",
        font=font_sm,
        fill=(200, 220, 255, 255),
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(OUT, "PNG", optimize=True)
    print("saved", OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main()
