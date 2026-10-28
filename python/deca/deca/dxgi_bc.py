"""
Decodeurs des blocs BC1 a BC5 (DXGI 70 a 84), selon les regles de Direct3D 10/11.

COTWed DECA (2026-10-28) : remplacent les decodeurs de dxgi.py, qui avaient quatre defauts (journal, D1 a D4) :
  D1  565 etendu par simple decalage (r << 3) au lieu de la conversion UNORM (r / 31 x 255) : 255 lu 248 ;
      de meme l'alpha 4 bits du BC2 (a << 4 : 255 lu 240) ;
  D2  bloc couleur du BC2 / BC3 lu comme du BC1 (3 couleurs et un noir si couleur0 <= couleur1) : en BC2 et BC3,
      le materiel lit toujours 4 couleurs ;
  D3  nx // 4 blocs par ligne au lieu de ceil(nx / 4) : un niveau de largeur non multiple de 4 (10 x 5, 2 x 1)
      etait lu avec des lignes decalees, et ses derniers pixels jamais ecrits ;
  D4  divisions tronquees au lieu d'arrondies (+-1).
Et les formats SNORM (BC4_SNORM 81, BC5_SNORM 84) etaient lus comme du UNORM.

Regles (spec. fonctionnelle de Direct3D 11, BC1 a BC5 ; DirectXTex les suit) :
  - extremites 565 : UNORM sur 5 ou 6 bits, valeur r5 / 31, g6 / 63 ; les couleurs intermediaires sont
    interpolees sur ces valeurs, (2 c0 + c1) / 3, (c0 + 2 c1) / 3 (BC1 a 3 couleurs : (c0 + c1) / 2), puis rendues
    sur 8 bits par round(v * 255). C'est le calcul de DirectXTex (DecodeBC1 : XMLoadU565 x (1/31, 1/63, 1/31),
    XMVectorLerp, puis UNORM 8 bits). En entiers, sans erreur d'arrondi : round(n x 255 / d) = (510 n + d) // 2d,
    avec n le numerateur sur les valeurs 5 / 6 bits et d = 31, 93 (tiers), 62 (moities) ou 63, 189, 126. Les tiers
    ne tombent jamais sur une demie ; les moities de somme 31 (5 bits) ou 63 (6 bits), 127,5, sont arrondies vers
    le haut. La recopie des bits de poids fort (r5 << 3 | r5 >> 2, Pillow, la plupart des decodeurs) en differe de 1
    sur 4 valeurs de 5 bits sur 32 et 10 de 6 bits sur 64 ; le decalage seul de l'ancien DECA, jusqu'a 7 ;
  - BC1 : couleur0 > couleur1 (comparaison des u16) -> 4 couleurs opaques ; sinon 3 couleurs et un noir transparent ;
  - BC2 : alpha explicite 4 bits par pixel, etendu par a4 * 17 ;
  - BC3 / BC4 / BC5 UNORM : alpha0 > alpha1 -> 6 intermediaires ((7 - k) a0 + k a1) / 7, sinon 4 intermediaires
    ((5 - k) a0 + k a1) / 5, puis 0 et 255 ; arrondis : (n + 3) // 7 et (n + 2) // 5 ;
  - BC4 / BC5 SNORM : extremites signees (-128 lu -127), memes interpolations en flottant sur [-1, 1], puis -1 et 1 ;
    rendues sur 8 bits par round((v + 1) * 127.5) : 0 = -1, 255 = +1 (une carte de normales s'affiche comme d'habitude) ;
  - BC4 : rouge seul (vert et bleu 0), BC5 : rouge et vert (bleu 0) ; alpha 255. Comme DECA avant : pas de
    reconstruction de la composante z d'une carte de normales.
  - Blocs : ceil(nx / 4) x ceil(ny / 4), lignes de blocs de haut en bas, pixels d'un bloc ligne par ligne, indices
    du poids faible au poids fort. Les pixels hors de l'image (bord d'un niveau de taille non multiple de 4) ne sont
    pas ecrits : l'image recue fait au moins max(4, nx) x max(4, ny) (ff_avtx.load_mip, ff_bc_raw).

Tous ecrivent dans image (ny, nx, 4) uint8, RGBA, depuis buffer (bytes) ; une donnee trop courte leve FFError.
"""
import numpy as np
from numba import njit

from .fast_file import raise_error


@njit(inline='always')
def _u16(buffer, pos):
    return np.int64(buffer[pos]) | (np.int64(buffer[pos + 1]) << 8)


@njit(inline='always')
def _u32(buffer, pos):
    return _u16(buffer, pos) | (_u16(buffer, pos + 2) << 16)


@njit(inline='always')
def _u48(buffer, pos):
    return _u32(buffer, pos) | (_u16(buffer, pos + 4) << 32)


@njit(inline='always')
def _check(nx, ny, n_buffer, block_bytes):
    """Nombre de blocs par ligne et par colonne ; leve FFError si la donnee est trop courte."""
    bnx = max(1, (nx + 3) // 4)
    bny = max(1, (ny + 3) // 4)
    if n_buffer < bnx * bny * block_bytes:
        raise_error()
    return bnx, bny


@njit(inline='always')
def _unorm8(num, den):
    """round(num * 255 / den), arrondi vers le haut sur une demie, en entiers."""
    return (510 * num + den) // (2 * den)


@njit(inline='always')
def color_palette(color0, color1, always_four, colors):
    """Palette (4, 4) RGBA d'un bloc couleur. always_four : BC2 et BC3 (jamais de mode 3 couleurs)."""
    for i in range(3):
        if i == 0:
            a, b, m = (color0 >> 11) & 0x1F, (color1 >> 11) & 0x1F, 31
        elif i == 1:
            a, b, m = (color0 >> 5) & 0x3F, (color1 >> 5) & 0x3F, 63
        else:
            a, b, m = color0 & 0x1F, color1 & 0x1F, 31
        colors[0, i] = _unorm8(a, m)
        colors[1, i] = _unorm8(b, m)
        if always_four or color0 > color1:
            colors[2, i] = _unorm8(2 * a + b, 3 * m)
            colors[3, i] = _unorm8(a + 2 * b, 3 * m)
        else:
            colors[2, i] = _unorm8(a + b, 2 * m)
            colors[3, i] = 0
    colors[0, 3] = 255
    colors[1, 3] = 255
    colors[2, 3] = 255
    colors[3, 3] = 255 if (always_four or color0 > color1) else 0


@njit(inline='always')
def alpha_palette(a0, a1, pal):
    """Palette (8,) d'un bloc alpha / BC4 UNORM."""
    pal[0] = a0
    pal[1] = a1
    if a0 > a1:
        for k in range(1, 7):
            pal[1 + k] = ((7 - k) * a0 + k * a1 + 3) // 7
    else:
        for k in range(1, 5):
            pal[1 + k] = ((5 - k) * a0 + k * a1 + 2) // 5
        pal[6] = 0
        pal[7] = 255


@njit(inline='always')
def alpha_palette_snorm(r0, r1, pal):
    """Palette (8,) d'un bloc BC4 SNORM, rendue sur 8 bits : round((v + 1) * 127.5), v dans [-1, 1]."""
    s0 = r0 - 256 if r0 >= 128 else r0
    s1 = r1 - 256 if r1 >= 128 else r1
    s0 = max(s0, -127)
    s1 = max(s1, -127)
    vals = np.zeros(8, dtype=np.float64)
    vals[0] = s0
    vals[1] = s1
    if s0 > s1:
        for k in range(1, 7):
            vals[1 + k] = ((7 - k) * s0 + k * s1) / 7.0
    else:
        for k in range(1, 5):
            vals[1 + k] = ((5 - k) * s0 + k * s1) / 5.0
        vals[6] = -127.0
        vals[7] = 127.0
    for k in range(8):
        pal[k] = np.int64(np.floor((vals[k] / 127.0 + 1.0) * 127.5 + 0.5))


@njit(inline='always')
def _put(image, y, x, c, v):
    if y < image.shape[0] and x < image.shape[1]:
        image[y, x, c] = v


@njit
def decode_bc1(image, buffer, n_buffer, nx, ny):  # DXGI 70, 71, 72
    bnx, bny = _check(nx, ny, n_buffer, 8)
    colors = np.zeros((4, 4), dtype=np.int64)
    pos = 0
    for yi in range(bny):
        for xi in range(bnx):
            color_palette(_u16(buffer, pos), _u16(buffer, pos + 2), False, colors)
            idx = _u32(buffer, pos + 4)
            pos += 8
            for p in range(16):
                k = (idx >> (2 * p)) & 3
                for c in range(4):
                    _put(image, yi * 4 + p // 4, xi * 4 + p % 4, c, colors[k, c])


@njit
def decode_bc2(image, buffer, n_buffer, nx, ny):  # DXGI 73, 74, 75
    bnx, bny = _check(nx, ny, n_buffer, 16)
    colors = np.zeros((4, 4), dtype=np.int64)
    pos = 0
    for yi in range(bny):
        for xi in range(bnx):
            alpha_lo = _u32(buffer, pos)
            alpha_hi = _u32(buffer, pos + 4)
            color_palette(_u16(buffer, pos + 8), _u16(buffer, pos + 10), True, colors)
            idx = _u32(buffer, pos + 12)
            pos += 16
            for p in range(16):
                k = (idx >> (2 * p)) & 3
                a4 = (alpha_lo >> (4 * p)) & 0xF if p < 8 else (alpha_hi >> (4 * (p - 8))) & 0xF
                y = yi * 4 + p // 4
                x = xi * 4 + p % 4
                for c in range(3):
                    _put(image, y, x, c, colors[k, c])
                _put(image, y, x, 3, a4 * 17)


@njit
def decode_bc3(image, buffer, n_buffer, nx, ny):  # DXGI 76, 77, 78
    bnx, bny = _check(nx, ny, n_buffer, 16)
    colors = np.zeros((4, 4), dtype=np.int64)
    alpha = np.zeros(8, dtype=np.int64)
    pos = 0
    for yi in range(bny):
        for xi in range(bnx):
            alpha_palette(np.int64(buffer[pos]), np.int64(buffer[pos + 1]), alpha)
            aidx = _u48(buffer, pos + 2)
            color_palette(_u16(buffer, pos + 8), _u16(buffer, pos + 10), True, colors)
            idx = _u32(buffer, pos + 12)
            pos += 16
            for p in range(16):
                k = (idx >> (2 * p)) & 3
                y = yi * 4 + p // 4
                x = xi * 4 + p % 4
                for c in range(3):
                    _put(image, y, x, c, colors[k, c])
                _put(image, y, x, 3, alpha[(aidx >> (3 * p)) & 7])


@njit
def _decode_bc45(image, buffer, n_buffer, nx, ny, channels, signed):
    bnx, bny = _check(nx, ny, n_buffer, 8 * channels)
    pal = np.zeros((2, 8), dtype=np.int64)
    idx = np.zeros(2, dtype=np.int64)
    pos = 0
    for yi in range(bny):
        for xi in range(bnx):
            for ch in range(channels):
                r0 = np.int64(buffer[pos])
                r1 = np.int64(buffer[pos + 1])
                if signed:
                    alpha_palette_snorm(r0, r1, pal[ch])
                else:
                    alpha_palette(r0, r1, pal[ch])
                idx[ch] = _u48(buffer, pos + 2)
                pos += 8
            for p in range(16):
                y = yi * 4 + p // 4
                x = xi * 4 + p % 4
                for ch in range(2):
                    _put(image, y, x, ch, pal[ch, (idx[ch] >> (3 * p)) & 7] if ch < channels else 0)
                _put(image, y, x, 2, 0)
                _put(image, y, x, 3, 255)


@njit
def decode_bc4(image, buffer, n_buffer, nx, ny):  # DXGI 79, 80
    _decode_bc45(image, buffer, n_buffer, nx, ny, 1, False)


@njit
def decode_bc4_snorm(image, buffer, n_buffer, nx, ny):  # DXGI 81
    _decode_bc45(image, buffer, n_buffer, nx, ny, 1, True)


@njit
def decode_bc5(image, buffer, n_buffer, nx, ny):  # DXGI 82, 83
    _decode_bc45(image, buffer, n_buffer, nx, ny, 2, False)


@njit
def decode_bc5_snorm(image, buffer, n_buffer, nx, ny):  # DXGI 84
    _decode_bc45(image, buffer, n_buffer, nx, ny, 2, True)


# format DXGI exact -> decodeur (les SNORM ne partagent pas le decodeur de leur format de base)
DECODERS = {
    70: decode_bc1, 71: decode_bc1, 72: decode_bc1,
    73: decode_bc2, 74: decode_bc2, 75: decode_bc2,
    76: decode_bc3, 77: decode_bc3, 78: decode_bc3,
    79: decode_bc4, 80: decode_bc4, 81: decode_bc4_snorm,
    82: decode_bc5, 83: decode_bc5, 84: decode_bc5_snorm,
}
