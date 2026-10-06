#!/usr/bin/env python3
"""
texture_fabrique.py -- images et icones .ddsc synthetiques pour les tests, le projet demo et les essais en jeu.

Aucune image du jeu ici : les cartouches sont dessinees avec des formes simples (PIL), sur fond transparent, a la
maniere des icones de munitions (objets centres, marges transparentes). Les couleurs vives des pointes rendent une
icone fabriquee impossible a confondre avec celles du jeu au magasin.
"""
import sys
from pathlib import Path
from typing import Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import icone as I  # noqa: E402

Couleur = Tuple[int, int, int]
LAITON: Couleur = (196, 156, 72)
ROUGE: Couleur = (220, 30, 30)
VERT: Couleur = (40, 200, 90)


def cartouches(w: int, h: int, pointe: Couleur = ROUGE, bande: Couleur = VERT, douille: Couleur = LAITON) -> np.ndarray:
    """RGBA w x h : trois cartouches debout et une couchee, bords lisses (dessin 4 fois plus grand, puis reduit en
    tenant compte de l'alpha)."""
    from PIL import Image, ImageDraw
    k = 4
    W, H = w * k, h * k
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    dark = tuple(int(c * 0.6) for c in douille)

    def debout(cx: float, top: float, bottom: float, width: float):
        x0, x1 = cx - width / 2, cx + width / 2
        neck = top + (bottom - top) * 0.32
        d.rounded_rectangle([x0, neck, x1, bottom], radius=width * 0.15, fill=douille + (255,), outline=dark + (255,),
                            width=max(1, k))
        d.rectangle([x0, bottom - width * 0.25, x1, bottom - width * 0.12], fill=dark + (255,))
        d.rectangle([x0 + width * 0.08, neck + (bottom - neck) * 0.45, x1 - width * 0.08,
                     neck + (bottom - neck) * 0.55], fill=bande + (255,))
        d.polygon([(x0 + width * 0.12, neck), (cx, top), (x1 - width * 0.12, neck)], fill=pointe + (255,))

    width = W * 0.07
    for i, cx in enumerate((W * 0.22, W * 0.34, W * 0.46)):
        debout(cx, H * (0.12 + 0.03 * (i % 2)), H * 0.86, width)
    # cartouche couchee, en bas a droite
    lying = Image.new('RGBA', (int(H * 0.70), int(width) + 2 * k), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lying)
    lw, lh = lying.size
    neck = lw * 0.68
    ld.rounded_rectangle([0, 0, neck, lh - 1], radius=lh * 0.15, fill=douille + (255,), outline=dark + (255,), width=max(1, k))
    ld.rectangle([lw * 0.30, lh * 0.1, lw * 0.36, lh * 0.9], fill=bande + (255,))
    ld.polygon([(neck, lh * 0.12), (lw - 1, lh / 2), (neck, lh * 0.88)], fill=pointe + (255,))
    lying = lying.rotate(18, expand=True, resample=Image.BICUBIC)
    img.alpha_composite(lying, (int(W * 0.52), int(H * 0.50)))
    small = img.convert('RGBa').resize((w, h), Image.LANCZOS).convert('RGBA')
    return np.asarray(small, dtype=np.uint8).copy()


def icone_ddsc(w: int = 64, h: int = 32, n: int = 7, pointe: Couleur = ROUGE, bande: Couleur = VERT) -> bytes:
    """Icone .ddsc (AVTX, BC3, n niveaux) de la forme des icones du jeu, en plus petit par defaut (64 x 32)."""
    return I.ddsc_bc3(cartouches(w, h, pointe, bande), n)


def main(argv=None):
    """Ecrit un PNG de cartouches (pour un essai en jeu) : texture_fabrique.py SORTIE.png [LARGEUR HAUTEUR]."""
    from PIL import Image
    argv = sys.argv[1:] if argv is None else argv
    w, h = (int(argv[1]), int(argv[2])) if len(argv) >= 3 else (640, 320)
    Image.fromarray(cartouches(w, h), 'RGBA').save(argv[0], optimize=True)
    print('Ecrit : {} ({}x{})'.format(argv[0], w, h))


if __name__ == '__main__':
    main()
