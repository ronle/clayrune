"""Generate Play Store listing assets from existing brand source files.

Outputs (to docs/play-store/assets/):
- icon-512.png        — required, 512x512 app icon for the listing
- feature-1024x500.png — required, 1024x500 banner shown above the icon

Sources:
- Claydo mascot: E:/clayrune-mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_foreground.png
- Brand palette: clayrune_website/styles.css (paper #F2E9D8, gold #D7B470, ink #191512)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "play-store" / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 432x432 Claydo mascot on transparent background. Best source we have today.
CLAYDO_SRC = Path("E:/clayrune-mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_foreground.png")

# ── Brand colors (from clayrune.io styles.css) ──────────────────────────────
PAPER  = (242, 233, 216)   # #F2E9D8 — warm cream body bg
GOLD   = (215, 180, 112)   # #D7B470 — primary accent
BRONZE = (196, 152, 73)    # #C49849 — gold deeper
INK    = ( 25,  21,  18)   # #191512 — deep warm ink
BROWN  = ( 58,  37,  23)   # #3A2517 — warm brown


# ── Helpers ──────────────────────────────────────────────────────────────────

def _radial_gradient(size: tuple[int, int], inner: tuple[int, int, int],
                     outer: tuple[int, int, int]) -> Image.Image:
    """Soft radial gradient from center outward. Used as the icon bg."""
    w, h = size
    img = Image.new("RGB", size, outer)
    px = img.load()
    cx, cy = w / 2, h / 2
    max_d = ((cx ** 2 + cy ** 2) ** 0.5)
    for y in range(h):
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            t = min(1.0, d / max_d)
            r = int(inner[0] * (1 - t) + outer[0] * t)
            g = int(inner[1] * (1 - t) + outer[1] * t)
            b = int(inner[2] * (1 - t) + outer[2] * t)
            px[x, y] = (r, g, b)
    return img


def _linear_gradient_h(size: tuple[int, int], left: tuple[int, int, int],
                       right: tuple[int, int, int]) -> Image.Image:
    """Horizontal gradient. Used as feature-graphic bg."""
    w, h = size
    img = Image.new("RGB", size, left)
    px = img.load()
    for x in range(w):
        t = x / max(1, w - 1)
        r = int(left[0] * (1 - t) + right[0] * t)
        g = int(left[1] * (1 - t) + right[1] * t)
        b = int(left[2] * (1 - t) + right[2] * t)
        for y in range(h):
            px[x, y] = (r, g, b)
    return img


def _pick_font(size: int) -> ImageFont.FreeTypeFont:
    """Best-effort font lookup with fallback to PIL default."""
    candidates = [
        # Bundled with the Fraunces family on the website — try Windows fonts dir
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",  # bold variant
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── Icon (512x512) ──────────────────────────────────────────────────────────

def make_icon() -> Path:
    """Square 512x512 icon. Full-bleed; Play Store auto-crops to its own
    mask (circle / squircle / etc.) so don't try to round corners here.
    Centered Claydo, warm-cream radial bg, subtle gold ring for definition."""
    sz = 512
    # Slight light center → cream edges so the icon "pops" without text.
    bg = _radial_gradient((sz, sz), (250, 244, 230), PAPER)

    # Subtle gold ring — concentric vignette using a transparent overlay so
    # the bg image stays RGB (PNG output stays smaller without unneeded alpha).
    overlay = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    inset = 12
    odraw.ellipse([inset, inset, sz - inset, sz - inset], outline=(*GOLD, 38), width=6)
    bg.paste(overlay, (0, 0), overlay)

    # Place Claydo, scaled so it fills ~76% of the icon with comfortable padding.
    claydo = Image.open(CLAYDO_SRC).convert("RGBA")
    target_w = int(sz * 0.76)
    ratio = target_w / claydo.width
    target_h = int(claydo.height * ratio)
    claydo = claydo.resize((target_w, target_h), Image.LANCZOS)

    # Soft shadow beneath the mascot for depth.
    shadow_blur = 14
    shadow = Image.new("RGBA", (target_w + shadow_blur * 4, target_h + shadow_blur * 4), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    ellipse_top = target_h + shadow_blur
    ellipse_bot = ellipse_top + int(target_h * 0.16)
    shadow_draw.ellipse([
        shadow_blur * 2, ellipse_top,
        shadow_blur * 2 + target_w, ellipse_bot,
    ], fill=(80, 50, 30, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
    sx = (sz - shadow.width) // 2
    sy = (sz - target_h) // 2 + 12
    bg_rgba = bg.convert("RGBA")
    bg_rgba.alpha_composite(shadow, (sx, sy))

    # Center the mascot. Nudge up slightly so the body's visual weight is centered.
    cx = (sz - target_w) // 2
    cy = (sz - target_h) // 2 - 8
    bg_rgba.alpha_composite(claydo, (cx, cy))

    out = OUT_DIR / "icon-512.png"
    bg_rgba.convert("RGB").save(out, "PNG", optimize=True)
    return out


# ── Feature graphic (1024x500) ──────────────────────────────────────────────

def make_feature_graphic() -> Path:
    """1024x500 banner. Shown ABOVE the icon on the Play Store listing.
    No critical text near the edges (Play might overlay UI on the right
    24px on small screens). Keep the wordmark + tagline in the inner ~80%."""
    w, h = 1024, 500
    # Gradient from cream → soft gold so Claydo on the left has good contrast.
    bg = _linear_gradient_h((w, h), PAPER, (231, 212, 168))
    bg = bg.convert("RGBA")

    # Subtle horizontal grid texture, very faint, for "engineering tool" vibe.
    grid_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grid_overlay)
    for x in range(0, w, 32):
        gdraw.line([(x, 0), (x, h)], fill=(*BROWN, 14), width=1)
    for y in range(0, h, 32):
        gdraw.line([(0, y), (w, y)], fill=(*BROWN, 14), width=1)
    bg.alpha_composite(grid_overlay)

    # Claydo on the left — sized to fit the left ~40% of the canvas.
    claydo = Image.open(CLAYDO_SRC).convert("RGBA")
    target_h = int(h * 0.78)
    ratio = target_h / claydo.height
    target_w = int(claydo.width * ratio)
    claydo = claydo.resize((target_w, target_h), Image.LANCZOS)
    cx, cy = 60, (h - target_h) // 2
    bg.alpha_composite(claydo, (cx, cy))

    # Wordmark + tagline on the right.
    draw = ImageDraw.Draw(bg)
    title_font = _pick_font(96)
    sub_font   = _pick_font(34)
    micro_font = _pick_font(22)

    title_x = cx + target_w + 32
    title_y = int(h * 0.30)
    draw.text((title_x, title_y), "Clayrune", font=title_font, fill=INK)

    sub_y = title_y + 110
    draw.text((title_x, sub_y), "Run your fleet from your phone.", font=sub_font, fill=BROWN)

    micro_y = sub_y + 56
    draw.text((title_x, micro_y), "Bring your own server · no cloud markup",
              font=micro_font, fill=(*BROWN, 200))

    out = OUT_DIR / "feature-1024x500.png"
    bg.convert("RGB").save(out, "PNG", optimize=True)
    return out


if __name__ == "__main__":
    icon = make_icon()
    feature = make_feature_graphic()
    for p in (icon, feature):
        sz = p.stat().st_size
        print(f"  {p.relative_to(REPO_ROOT)}  ({sz // 1024} KB)")
