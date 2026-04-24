"""Generate Mission Control source icon (1024x1024) matching the favicon:
rounded #e8824a square with white 'M'. Output is src-tauri/mc_icon_source.png."""
from PIL import Image, ImageDraw, ImageFont
import os, sys

SIZE = 1024
RADIUS = int(SIZE * 7 / 32)  # matches favicon viewBox ratio (rx=7 on 32)
BG = (232, 130, 74, 255)     # #e8824a
FG = (255, 255, 255, 255)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=RADIUS, fill=BG)

font = None
candidates = [
    r"C:\Windows\Fonts\Nunito-Black.ttf",
    r"C:\Windows\Fonts\Nunito-ExtraBold.ttf",
    r"C:\Windows\Fonts\Nunito-Bold.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
]
for path in candidates:
    if os.path.exists(path):
        font = ImageFont.truetype(path, int(SIZE * 0.62))
        print(f"using font: {path}")
        break
if font is None:
    print("no TTF found, falling back to default", file=sys.stderr)
    font = ImageFont.load_default()

text = "M"
bbox = draw.textbbox((0, 0), text, font=font)
tw = bbox[2] - bbox[0]
th = bbox[3] - bbox[1]
x = (SIZE - tw) / 2 - bbox[0]
y = (SIZE - th) / 2 - bbox[1]
draw.text((x, y), text, font=font, fill=FG)

out = os.path.join(os.path.dirname(__file__), "mc_icon_source.png")
img.save(out, "PNG")
print(f"wrote {out}  {img.size}")
