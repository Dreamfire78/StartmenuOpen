"""Builds the .ico files from startmenu_icon.svg (build-time only: pymupdf, pillow).

startmenu_open.ico                program icon (exe)                 -> APP_COLOR
startmenu_open_light.ico          tray icon for a dark taskbar       -> white
startmenu_open_dark.ico           tray icon for a light taskbar      -> near black
startmenu_open_light_paused.ico   same, faded (feature switched off)
startmenu_open_dark_paused.ico
"""

import io
import re

import pymupdf
from PIL import Image

SVG_FILE = "startmenu_icon.svg"
APP_COLOR = "#0078D4"
PAUSED_OPACITY = 0.4
ICONS = {
    # filename: (color, opacity)
    "startmenu_open.ico": (APP_COLOR, 1.0),
    "startmenu_open_light.ico": ("#FFFFFF", 1.0),
    "startmenu_open_dark.ico": ("#1F1F1F", 1.0),
    "startmenu_open_light_paused.ico": ("#FFFFFF", PAUSED_OPACITY),
    "startmenu_open_dark_paused.ico": ("#1F1F1F", PAUSED_OPACITY),
}
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(svg_text, color, opacity, size):
    svg = svg_text.replace("currentColor", color)
    # Replace "1em" etc. with a fixed size; drawing is driven by the viewBox.
    svg = re.sub(r'(<svg[^>]*?)\swidth="[^"]*"', r'\1 width="24"', svg, count=1)
    svg = re.sub(r'(<svg[^>]*?)\sheight="[^"]*"', r'\1 height="24"', svg, count=1)
    doc = pymupdf.open(stream=svg.encode("utf-8"), filetype="svg")
    page = doc[0]
    zoom = size / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    if opacity < 1.0:
        alpha = img.getchannel("A").point(lambda a: round(a * opacity))
        img.putalpha(alpha)
    return img


def main():
    with open(SVG_FILE, encoding="utf-8") as f:
        svg_text = f.read()
    for filename, (color, opacity) in ICONS.items():
        images = [render(svg_text, color, opacity, s) for s in SIZES]
        # Every size is rendered separately -> sharp lines even at 16/24 px.
        images[-1].save(filename, sizes=[(s, s) for s in SIZES], append_images=images[:-1])
        print(f"{filename} created ({color}, opacity {opacity})")


if __name__ == "__main__":
    main()
