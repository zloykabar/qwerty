# -*- coding: utf-8 -*-
"""Фирменный значок Джарвиса: тёмный шар с голубым свечением. Рисуется кодом, файлов не нужно.
python brand.py  -> создаёт assets/app.ico"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter


def make_icon_image(size=256):
    k = 4                                    # рисуем крупно и уменьшаем: края получаются гладкими
    S = size * k
    c = S / 2
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([c - S * .47, c - S * .47, c + S * .47, c + S * .47], fill=(0, 190, 255, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(S * .05))
    img.alpha_composite(glow)

    d = ImageDraw.Draw(img)
    r = S * .43
    d.ellipse([c - r, c - r, c + r, c + r], fill=(6, 12, 26, 255))
    for rr, w, col in ((.43, .018, (0, 200, 255, 255)), (.33, .012, (70, 170, 255, 230)),
                       (.23, .010, (0, 220, 255, 200))):
        R = S * rr
        d.ellipse([c - R, c - R, c + R, c + R], outline=col, width=int(S * w))
    # дуги, как у шкалы
    R = S * .38
    for a0 in (20, 140, 260):
        d.arc([c - R, c - R, c + R, c + R], a0, a0 + 70, fill=(190, 240, 255, 255), width=int(S * .02))
    # светящееся ядро
    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(core)
    r0 = S * .13
    cd.ellipse([c - r0, c - r0, c + r0, c + r0], fill=(150, 235, 255, 255))
    core_blur = core.filter(ImageFilter.GaussianBlur(S * .025))
    img.alpha_composite(core_blur)
    img.alpha_composite(core)
    # лёгкие лучи
    for i in range(6):
        a = math.radians(i * 60 + 30)
        d.line([c + math.cos(a) * S * .16, c + math.sin(a) * S * .16,
                c + math.cos(a) * S * .21, c + math.sin(a) * S * .21], fill=(0, 200, 255, 255), width=int(S * .012))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path):
    make_icon_image(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    save_ico(target)
    print("assets/app.ico создан")
