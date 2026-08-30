#!/usr/bin/env python3
"""Schneidet einen ganzseitigen 2x-Schuss in Handy-Bildschirme (390x844 CSS-px)."""
import sys, os
from PIL import Image


def schneide(quelle, ziel_ordner, praefix, ab=0, bis=None, hoehe=844):
    im = Image.open(quelle)
    b, h = im.size
    skala = b / 390.0
    im = im.resize((390, int(h / skala)), Image.LANCZOS)
    h = im.size[1]
    os.makedirs(ziel_ordner, exist_ok=True)
    raus, n, y = [], 0, ab
    ende = bis if bis is not None else h
    while y < ende:
        u = min(y + hoehe, h)
        p = os.path.join(ziel_ordner, f"{praefix}_{n:02d}.png")
        im.crop((0, y, 390, u)).save(p)
        raus.append(p)
        n += 1
        y += hoehe
    return raus


if __name__ == "__main__":
    q, ziel = sys.argv[1], sys.argv[2]
    praefix = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(os.path.basename(q))[0]
    ab = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    bis = int(sys.argv[5]) if len(sys.argv) > 5 else None
    for p in schneide(q, ziel, praefix, ab, bis):
        print(p)
