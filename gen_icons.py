"""Bake the tray glyph to static .ico files in assets/.

main.py loads these from disk rather than drawing fresh on every run/state
change -- Windows keys "always show in tray" off icon identity, and a
freshly-rendered bitmap each time made it treat the icon as new and silently
re-hide it in the overflow.

Run again only if you change the glyph design:

    .venv\\Scripts\\python.exe gen_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def make_icon_image(active):
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    body = (76, 194, 255) if active else (220, 220, 220)

    draw.polygon([(14, 26), (24, 26), (36, 14), (36, 50), (24, 38), (14, 38)],
                 fill=body)
    for i, radius in enumerate((10, 17)):
        box = (36 - radius // 2, 32 - radius, 36 + radius + 4, 32 + radius)
        draw.arc(box, start=-55, end=55, fill=body, width=4 - i)
    return image


def main():
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)]

    make_icon_image(False).save(ASSETS_DIR / "tray_inactive.ico", format="ICO", sizes=sizes)
    make_icon_image(True).save(ASSETS_DIR / "tray_active.ico", format="ICO", sizes=sizes)

    print("wrote", ASSETS_DIR / "tray_inactive.ico")
    print("wrote", ASSETS_DIR / "tray_active.ico")


if __name__ == "__main__":
    main()
