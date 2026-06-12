# Generate the built-in dashboard background images (WhatsApp/Telegram-style
# tone-on-tone doodle patterns, MC-themed icon set) into static/backgrounds/.
#
# Run after changing a recipe:  python tools/generate-backgrounds.py
# Outputs <id>.webp (2560x1440, the bg itself) + <id>-thumb.webp (384x216,
# settings-gallery preview). The frontend registry of these ids lives in
# static/js/appearance.js (BUILTIN_BGS) — keep the two in sync.
import math
import os
import random
from PIL import Image, ImageDraw, ImageFilter

W, H = 2560, 1440
OUT = os.path.join(os.path.dirname(__file__), "..", "static", "backgrounds")
RS = Image.Resampling  # Pillow >= 9.1


def hex_rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# ── icon set ─────────────────────────────────────────────────────────────
# Each draws into a unit box scaled by S with stroke width w (rounded joints).

def _pts(S, pts):
    return [(x * S, y * S) for x, y in pts]


def icon_terminal(d, S, c, w):
    d.rounded_rectangle([0.08 * S, 0.16 * S, 0.92 * S, 0.84 * S], radius=0.1 * S, outline=c, width=w)
    d.line(_pts(S, [(0.22, 0.38), (0.36, 0.50), (0.22, 0.62)]), fill=c, width=w, joint="curve")
    d.line(_pts(S, [(0.44, 0.64), (0.62, 0.64)]), fill=c, width=w)


def icon_branch(d, S, c, w):
    d.ellipse([0.17 * S, 0.10 * S, 0.33 * S, 0.26 * S], outline=c, width=w)
    d.ellipse([0.17 * S, 0.74 * S, 0.33 * S, 0.90 * S], outline=c, width=w)
    d.ellipse([0.67 * S, 0.26 * S, 0.83 * S, 0.42 * S], outline=c, width=w)
    d.line(_pts(S, [(0.25, 0.26), (0.25, 0.74)]), fill=c, width=w)
    d.line(_pts(S, [(0.25, 0.58), (0.30, 0.46), (0.52, 0.37), (0.67, 0.35)]), fill=c, width=w, joint="curve")


def icon_bubble(d, S, c, w):
    d.rounded_rectangle([0.12 * S, 0.18 * S, 0.88 * S, 0.68 * S], radius=0.16 * S, outline=c, width=w)
    d.line(_pts(S, [(0.32, 0.68), (0.27, 0.86), (0.48, 0.68)]), fill=c, width=w, joint="curve")
    for x in (0.34, 0.50, 0.66):
        r = 0.035 * S
        d.ellipse([x * S - r, 0.43 * S - r, x * S + r, 0.43 * S + r], fill=c)


def icon_gear(d, S, c, w):
    d.ellipse([0.32 * S, 0.32 * S, 0.68 * S, 0.68 * S], outline=c, width=w)
    r = 0.045 * S
    d.ellipse([0.5 * S - r, 0.5 * S - r, 0.5 * S + r, 0.5 * S + r], outline=c, width=max(2, w // 2))
    for k in range(8):
        a = math.radians(k * 45)
        x1, y1 = 0.5 + 0.21 * math.cos(a), 0.5 + 0.21 * math.sin(a)
        x2, y2 = 0.5 + 0.30 * math.cos(a), 0.5 + 0.30 * math.sin(a)
        d.line(_pts(S, [(x1, y1), (x2, y2)]), fill=c, width=w)


def icon_rocket(d, S, c, w):
    d.line(_pts(S, [(0.5, 0.10), (0.64, 0.30), (0.64, 0.62), (0.36, 0.62), (0.36, 0.30), (0.5, 0.10)]),
           fill=c, width=w, joint="curve")
    r = 0.055 * S
    d.ellipse([0.5 * S - r, 0.36 * S - r, 0.5 * S + r, 0.36 * S + r], outline=c, width=max(2, w // 2))
    d.line(_pts(S, [(0.36, 0.52), (0.24, 0.72), (0.36, 0.68)]), fill=c, width=w, joint="curve")
    d.line(_pts(S, [(0.64, 0.52), (0.76, 0.72), (0.64, 0.68)]), fill=c, width=w, joint="curve")
    d.line(_pts(S, [(0.46, 0.68), (0.5, 0.84), (0.54, 0.68)]), fill=c, width=w, joint="curve")


def icon_robot(d, S, c, w):
    d.rounded_rectangle([0.22 * S, 0.32 * S, 0.78 * S, 0.78 * S], radius=0.12 * S, outline=c, width=w)
    for x in (0.38, 0.62):
        r = 0.04 * S
        d.ellipse([x * S - r, 0.55 * S - r, x * S + r, 0.55 * S + r], fill=c)
    d.line(_pts(S, [(0.5, 0.32), (0.5, 0.20)]), fill=c, width=w)
    r = 0.035 * S
    d.ellipse([0.5 * S - r, 0.16 * S - r, 0.5 * S + r, 0.16 * S + r], outline=c, width=max(2, w // 2))
    d.line(_pts(S, [(0.22, 0.55), (0.13, 0.55)]), fill=c, width=w)
    d.line(_pts(S, [(0.78, 0.55), (0.87, 0.55)]), fill=c, width=w)


def icon_code(d, S, c, w):
    d.line(_pts(S, [(0.34, 0.30), (0.16, 0.50), (0.34, 0.70)]), fill=c, width=w, joint="curve")
    d.line(_pts(S, [(0.66, 0.30), (0.84, 0.50), (0.66, 0.70)]), fill=c, width=w, joint="curve")
    d.line(_pts(S, [(0.56, 0.24), (0.44, 0.76)]), fill=c, width=w)


def icon_bolt(d, S, c, w):
    pts = [(0.56, 0.12), (0.34, 0.50), (0.47, 0.50), (0.42, 0.86), (0.68, 0.44), (0.53, 0.44), (0.56, 0.12)]
    d.line(_pts(S, pts), fill=c, width=w, joint="curve")


def icon_folder(d, S, c, w):
    pts = [(0.12, 0.34), (0.38, 0.34), (0.46, 0.42), (0.88, 0.42), (0.88, 0.74), (0.12, 0.74), (0.12, 0.34)]
    d.line(_pts(S, pts), fill=c, width=w, joint="curve")


def icon_clock(d, S, c, w):
    d.ellipse([0.20 * S, 0.20 * S, 0.80 * S, 0.80 * S], outline=c, width=w)
    d.line(_pts(S, [(0.5, 0.5), (0.5, 0.32)]), fill=c, width=w)
    d.line(_pts(S, [(0.5, 0.5), (0.64, 0.57)]), fill=c, width=w)


def icon_search(d, S, c, w):
    d.ellipse([0.20 * S, 0.20 * S, 0.62 * S, 0.62 * S], outline=c, width=w)
    d.line(_pts(S, [(0.59, 0.59), (0.80, 0.80)]), fill=c, width=w)


def icon_bell(d, S, c, w):
    d.arc([0.28 * S, 0.20 * S, 0.72 * S, 0.64 * S], 180, 360, fill=c, width=w)
    d.line(_pts(S, [(0.28, 0.42), (0.28, 0.62), (0.20, 0.62), (0.80, 0.62), (0.72, 0.62), (0.72, 0.42)]),
           fill=c, width=w, joint="curve")
    r = 0.04 * S
    d.ellipse([0.5 * S - r, 0.70 * S - r, 0.5 * S + r, 0.70 * S + r], outline=c, width=max(2, w // 2))


def icon_coffee(d, S, c, w):
    d.rounded_rectangle([0.26 * S, 0.38 * S, 0.62 * S, 0.74 * S], radius=0.08 * S, outline=c, width=w)
    d.arc([0.58 * S, 0.44 * S, 0.80 * S, 0.66 * S], -90, 90, fill=c, width=w)
    d.line(_pts(S, [(0.38, 0.30), (0.41, 0.20)]), fill=c, width=w)
    d.line(_pts(S, [(0.52, 0.30), (0.55, 0.20)]), fill=c, width=w)


def icon_plane(d, S, c, w):
    pts = [(0.14, 0.52), (0.86, 0.20), (0.56, 0.82), (0.45, 0.60), (0.14, 0.52)]
    d.line(_pts(S, pts), fill=c, width=w, joint="curve")
    d.line(_pts(S, [(0.45, 0.60), (0.86, 0.20)]), fill=c, width=w)


ICONS = [icon_terminal, icon_branch, icon_bubble, icon_gear, icon_rocket, icon_robot,
         icon_code, icon_bolt, icon_folder, icon_clock, icon_search, icon_bell,
         icon_coffee, icon_plane]


# ── pattern compositor ───────────────────────────────────────────────────

def doodle_layer(stroke_rgba, cell=185, seed=7):
    rng = random.Random(seed)  # fixed seed → reproducible asset
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cols = W // cell + 3
    rows = H // cell + 3
    seq = []
    while len(seq) < cols * rows:  # shuffled cycles so neighbours differ
        batch = ICONS[:]
        rng.shuffle(batch)
        seq.extend(batch)
    i = 0
    for r in range(rows):
        for q in range(cols):
            fn = seq[i]; i += 1
            size = rng.randint(78, 100)
            S = size * 3                      # 3x supersample
            tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d = ImageDraw.Draw(tile)
            fn(d, S, stroke_rgba, max(3, int(S * 0.052)))
            tile = tile.rotate(rng.uniform(-14, 14), resample=RS.BICUBIC, expand=True)
            tile = tile.resize((tile.width // 3, tile.height // 3), RS.LANCZOS)
            x = q * cell - cell // 2 + (cell // 2 if r % 2 else 0) + rng.randint(-12, 12)
            y = r * cell - cell // 2 + rng.randint(-12, 12)
            layer.alpha_composite(tile, (x, y))
    return layer


def glow(blobs, base_rgb):
    GW, GH = 640, 360
    img = Image.new("RGB", (GW, GH), base_rgb)
    for cx, cy, rx, ry, color, alpha in blobs:
        layer = Image.new("RGBA", (GW, GH), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.ellipse([(cx - rx) * GW, (cy - ry) * GH, (cx + rx) * GW, (cy + ry) * GH],
                  fill=hex_rgb(color) + (alpha,))
        layer = layer.filter(ImageFilter.GaussianBlur(60))
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    return img.resize((W, H), RS.BICUBIC)


def save(img, name, thumb_center_crop):
    full = os.path.join(OUT, f"{name}.webp")
    img.save(full, "WEBP", quality=85, method=6)
    if thumb_center_crop:
        # flat patterns: crop the middle at 3x so thumbnail icons stay legible
        t = img.crop((W // 2 - 576, H // 2 - 324, W // 2 + 576, H // 2 + 324))
    else:
        t = img  # gradients: the full frame is the point
    t = t.resize((384, 216), RS.LANCZOS)
    t.save(os.path.join(OUT, f"{name}-thumb.webp"), "WEBP", quality=80, method=6)
    print(f"{name:16s} {os.path.getsize(full) // 1024:5d} KB")


def main():
    os.makedirs(OUT, exist_ok=True)

    # doodle-dark: flat WhatsApp-style on the dark theme
    img = Image.new("RGB", (W, H), hex_rgb("#0c0e14")).convert("RGBA")
    img.alpha_composite(doodle_layer(hex_rgb("#2a3147") + (255,)))
    save(img.convert("RGB"), "doodle-dark", thumb_center_crop=True)

    # doodle-aurora: Telegram-style pattern over a soft gradient
    img = glow([
        (0.15, 0.05, 0.60, 0.65, "#1a3a6a", 190),
        (0.85, 0.30, 0.45, 0.50, "#3f2e80", 120),
        (0.45, 1.00, 0.55, 0.45, "#163326", 130),
    ], hex_rgb("#0c0e14")).convert("RGBA")
    img.alpha_composite(doodle_layer(hex_rgb("#9db8e8") + (40,), seed=11))
    save(img.convert("RGB"), "doodle-aurora", thumb_center_crop=False)

    # doodle-warm: WhatsApp-style on the warm cream tone
    img = Image.new("RGB", (W, H), hex_rgb("#f6f0e4")).convert("RGBA")
    img.alpha_composite(doodle_layer(hex_rgb("#ddd0b2") + (255,), seed=13))
    save(img.convert("RGB"), "doodle-warm", thumb_center_crop=True)


if __name__ == "__main__":
    main()
