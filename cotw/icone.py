#!/usr/bin/env python3
"""
icone.py -- icone d'objet (texture .ddsc, format AVTX) : lecture, et fabrication a partir d'une image (PNG...).

Icones des munitions (releve du 2026-10-06 sur les 116 fichiers de textures/ui/items/ammo/) : toutes identiques par la
forme. AVTX version 1, 640 x 320, DXGI_FORMAT_BC3_UNORM (77, "DXT5"), 9 niveaux de detail (640x320 ... 2x1), tous
dans le .ddsc (aucun .hmddsc ni .atx) ; en-tete de 128 octets, puis les niveaux du plus grand au plus petit (273 184
octets). Fond transparent (couche alpha), d'ou le suffixe "_alpha".

En-tete (lecture de deca.ff_avtx, valeurs des icones) :
  0x00 "AVTX"  0x04 u16 version 1  0x06 u8 4 (inconnu)  0x07 u8 dimension - 1 (2 : texture 2D)  0x08 u32 format 77
  0x0c u16 largeur 640  0x0e u16 hauteur 320  0x10 u16 profondeur 1  0x12 u16 drapeaux 0  0x14 u8 niveaux 9
  0x15 u8 niveaux dans le fichier 9  0x16..0x1f 0  0x20 u32 offset du corps 128  0x24 u32 taille du corps 273 184
  0x28 u32 16 (alignement, [hypothese]) puis des zeros jusqu'a 0x80.

Fabrication : l'en-tete est repris tel quel d'une icone du jeu (le modele : en general l'icone de la munition source),
seul le corps est refait. L'image est mise a la taille du modele (proportions gardees, centree, marges transparentes),
reduite pour chaque niveau en tenant compte de l'alpha (pas de liseré sombre autour des objets), puis compressee en
BC3 par ce module (numpy) : bloc alpha a 8 niveaux, bloc couleur a 4 couleurs (deux extremites prises sur l'axe
principal des pixels du bloc ponderes par leur alpha, puis affinees par moindres carres), toujours avec
couleur0 > couleur1, pour que les decodeurs qui traitent le bloc couleur comme du BC1 (deca.dxgi) lisent la meme
chose que le jeu. Mesure : 8 dB de mieux que l'encodeur DXT5 de Pillow 12.2 sur les memes images.
Controle du fichier produit, en deux lectures :
  - decodage de reference (decoder_bc3, ce module, regles de Direct3D : 565 etendu par recopie des bits de poids
    fort, 4 couleurs toujours, arrondis) : chaque niveau compare a l'image voulue, PSNR sur les couleurs des pixels
    d'alpha >= 128 et sur l'alpha ; 33 dB au moins pour le niveau 0 (s'il a au moins 128 px de haut, comme les
    icones du jeu), 24 dB pour les niveaux reduits jusqu'a 20x10 ;
    les niveaux plus petits (quelques pixels) ne sont pas notes ;
  - lecture par deca.ff_avtx (meme forme que le modele, decodeur independant) : elle doit concorder avec la
    reference, a l'approximation de deca.dxgi pres (565 etendu par simple decalage, r << 3 : 255 lu 248, et
    divisions tronquees) : 8 d'ecart au plus en couleur, 1 en alpha. Une erreur de disposition des bits ou des
    blocs donnerait bien plus.
Mesure sur les 6 icones relevees, reencodees depuis leur propre niveau 0 : niveau 0 a 45 a 49 dB, niveaux reduits
29 a 33 dB (les niveaux du jeu eux-memes, compares a une reduction de leur niveau 0 : 27 a 31 dB). Le decodage de
reference concorde avec deca.dxgi sur les icones du jeu (ecart maximal 7 en couleur, 1 en alpha).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\icone.py lire D:\\dev\\audits\\icones\\textures\\ui\\items\\ammo\\equipment_ammo_243_sp_01_alpha.ddsc --png sp.png
    python cotw\\icone.py fabriquer mon_icone.png --modele <icone du jeu .ddsc> --sortie mon_icone_alpha.ddsc
"""
import argparse
import io
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np

BC3_UNORM = 77
HEADER_SIZE = 128
PSNR_MIN = 33.0          # niveau 0 : en dessous, compression jugee mauvaise (icones du jeu reencodees : 45 a 49 dB)
COTE_MIN_NIVEAU_0 = 128  # ... s'il mesure au moins 128 px de haut ; plus petit, il est aussi dense qu'un niveau reduit
PSNR_MIN_NIVEAUX = 24.0  # niveaux reduits : plus de detail par bloc, donc plus d'ecart (le jeu lui-meme : 27 a 31 dB)
COTE_MIN_CONTROLE = 8    # niveaux dont le petit cote est plus court : non notes (10x5 et moins)
ECART_DECA_COULEUR = 8   # deca.dxgi etend le 565 par decalage (jusqu'a 7 de moins) et tronque ses divisions
ECART_DECA_ALPHA = 1     # deca.dxgi tronque les divisions de l'alpha


class EIcone(Exception):
    pass


@dataclass
class Forme:
    largeur: int
    hauteur: int
    format: int
    niveaux: int
    taille_entete: int
    taille_corps: int


def forme(data: bytes) -> Forme:
    if data[:4] != b'AVTX':
        raise EIcone('pas une texture AVTX (.ddsc)')
    fmt = struct.unpack_from('<I', data, 8)[0]
    w, h, depth = struct.unpack_from('<HHH', data, 12)
    flags = struct.unpack_from('<H', data, 18)[0]
    mips, in_file = data[20], data[21]
    head, body = struct.unpack_from('<II', data, 32)
    if depth != 1 or flags != 0 or data[7] != 2:
        raise EIcone('texture non prise en charge (profondeur {}, drapeaux 0x{:x}, dimension {})'.format(depth, flags, data[7] + 1))
    if mips != in_file:
        raise EIcone('{} niveaux dont {} dans le fichier : niveaux dans un autre fichier (.hmddsc), non pris en charge'.format(
            mips, in_file))
    if head + body != len(data):
        raise EIcone('taille {} au lieu de {} (en-tete {} + corps {})'.format(len(data), head + body, head, body))
    return Forme(w, h, fmt, mips, head, body)


def tailles_niveaux(w: int, h: int, n: int) -> List[Tuple[int, int]]:
    out = []
    for _ in range(n):
        out.append((w, h))
        w, h = max(1, w // 2), max(1, h // 2)
    return out


def taille_bc3(w: int, h: int) -> int:
    return 16 * max(1, (w + 3) // 4) * max(1, (h + 3) // 4)


def entete(w: int, h: int, n: int) -> bytes:
    """En-tete AVTX d'une icone BC3 de w x h a n niveaux, tous dans le fichier. Pour 640 x 320 et 9 niveaux : les 128
    premiers octets des icones de munitions du jeu (identiques dans les 6 icones relevees). Sert aux fixtures ; la
    fabrication reprend l'en-tete du modele."""
    body = sum(taille_bc3(*s) for s in tailles_niveaux(w, h, n))
    head = b'AVTX' + struct.pack('<HBBIHHHHBB', 1, 4, 2, BC3_UNORM, w, h, 1, 0, n, n) + bytes(10)
    head += struct.pack('<III', HEADER_SIZE, body, 16)
    return head + bytes(HEADER_SIZE - len(head))


def ddsc_bc3(rgba: np.ndarray, n: int) -> bytes:
    """Icone .ddsc complete (en-tete et n niveaux) d'une image RGBA deja a la bonne taille. Sert aux fixtures."""
    h, w = rgba.shape[:2]
    sizes = tailles_niveaux(w, h, n)
    return entete(w, h, n) + b''.join(encoder_bc3(lv) for lv in niveaux(rgba, sizes))


# ---------------------------------------------------------------------------
# Lecture (deca.ff_avtx, decodeur independant de l'encodeur de ce module)
# ---------------------------------------------------------------------------

def lire(data: bytes) -> List[np.ndarray]:
    """Images RGBA (uint8, hauteur x largeur x 4) de chaque niveau, du plus grand au plus petit."""
    from deca.ff_avtx import Ddsc
    from deca.file import ArchiveFile
    f = forme(data)
    dd = Ddsc()
    with ArchiveFile(io.BytesIO(data)) as fh:
        dd.load_ddsc(fh, save_raw_data=True)
    out = []
    for (w, h), mip in zip(tailles_niveaux(f.largeur, f.hauteur, f.niveaux), dd.mips):
        out.append(np.asarray(mip.data)[:h, :w, :4].astype(np.uint8))
    return out


# ---------------------------------------------------------------------------
# Image a la forme du modele, niveaux de detail
# ---------------------------------------------------------------------------

def preparer(image, w: int, h: int) -> Tuple[np.ndarray, List[str]]:
    """Image PIL (ou chemin) -> RGBA w x h : proportions gardees, centree, marges transparentes."""
    from PIL import Image
    notes = []
    img = image if isinstance(image, Image.Image) else Image.open(image)
    if img.mode not in ('RGBA', 'LA', 'PA') and 'transparency' not in img.info:
        notes.append('image sans transparence : le fond sera visible au magasin')
    img = img.convert('RGBA')
    if img.size != (w, h):
        s = min(w / img.size[0], h / img.size[1])
        size = (max(1, round(img.size[0] * s)), max(1, round(img.size[1] * s)))
        notes.append('image {}x{} mise a {}x{} (proportions gardees), centree dans {}x{}'.format(
            img.size[0], img.size[1], size[0], size[1], w, h))
        small = img.convert('RGBa').resize(size, Image.LANCZOS).convert('RGBA')
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        canvas.paste(small, ((w - size[0]) // 2, (h - size[1]) // 2))
        img = canvas
    return np.asarray(img, dtype=np.uint8).copy(), notes


def niveaux(rgba: np.ndarray, sizes: List[Tuple[int, int]]) -> List[np.ndarray]:
    """Reductions successives, couleurs ponderees par l'alpha (sinon le fond transparent, souvent noir, assombrit
    les bords des objets)."""
    from PIL import Image
    base = Image.fromarray(rgba, 'RGBA').convert('RGBa')
    out = [rgba]
    for w, h in sizes[1:]:
        out.append(np.asarray(base.resize((w, h), Image.BOX).convert('RGBA'), dtype=np.uint8).copy())
    return out


# ---------------------------------------------------------------------------
# Compression BC3 (DXT5)
# ---------------------------------------------------------------------------

def _blocs(rgba: np.ndarray) -> np.ndarray:
    """(nby, nbx, 16, 4) float32 ; image completee jusqu'a un multiple de 4 en recopiant les bords."""
    h, w, _ = rgba.shape
    H, W = max(4, -(-h // 4) * 4), max(4, -(-w // 4) * 4)
    pad = np.pad(rgba, ((0, H - h), (0, W - w), (0, 0)), mode='edge')
    b = pad.reshape(H // 4, 4, W // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(H // 4, W // 4, 16, 4)
    return b.astype(np.float32)


def _565(c: np.ndarray) -> np.ndarray:
    r = np.clip(np.round(c[..., 0] * 31 / 255), 0, 31).astype(np.uint32)
    g = np.clip(np.round(c[..., 1] * 63 / 255), 0, 63).astype(np.uint32)
    b = np.clip(np.round(c[..., 2] * 31 / 255), 0, 31).astype(np.uint32)
    return (r << 11) | (g << 5) | b


def _565_rgb(v: np.ndarray) -> np.ndarray:
    r = (v >> 11) & 31
    g = (v >> 5) & 63
    b = v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1).astype(np.float32)


def _alpha_bloc(a: np.ndarray) -> np.ndarray:
    """a : (n, 16) -> (n, 8) octets : a0 >= a1, 8 niveaux (6 interpoles) si a0 > a1."""
    a0 = a.max(axis=1)
    a1 = a.min(axis=1)
    pal = np.stack([a0, a1] + [((7 - k) * a0 + k * a1) / 7 for k in range(1, 7)], axis=1)   # codes 0..7
    idx = np.abs(a[:, :, None] - pal[:, None, :]).argmin(axis=2).astype(np.uint64)
    idx[a0 == a1] = 0
    bits = np.zeros(len(a), dtype=np.uint64)
    for i in range(16):
        bits |= idx[:, i] << np.uint64(3 * i)
    out = np.zeros((len(a), 8), dtype=np.uint8)
    out[:, 0] = np.round(a0).astype(np.uint8)
    out[:, 1] = np.round(a1).astype(np.uint8)
    for k in range(6):
        out[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(0xff)).astype(np.uint8)
    return out


def _assigner(rgb: np.ndarray, w: np.ndarray, e0: np.ndarray, e1: np.ndarray):
    """Extremites -> (couleur0, couleur1 en 565 avec couleur0 >= couleur1, indices, erreur ponderee)."""
    c0, c1 = _565(e0), _565(e1)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _565_rgb(c0), _565_rgb(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], axis=1)          # (n, 4, 3)
    dist = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(axis=3)
    idx = dist.argmin(axis=2).astype(np.uint32)
    idx[c0 == c1] = 0
    err = (np.take_along_axis(dist, idx[..., None].astype(np.int64), axis=2)[..., 0] * w).sum(axis=1)
    return c0, c1, idx, err


def _couleur_bloc(rgb: np.ndarray, w: np.ndarray) -> np.ndarray:
    """rgb : (n, 16, 3), w : (n, 16) poids (pixels visibles) -> (n, 8) octets, toujours couleur0 > couleur1."""
    n = len(rgb)
    wsum = w.sum(axis=1, keepdims=True)
    w = np.where(wsum > 0, w, 1.0)                      # bloc entierement transparent : tous les pixels comptent
    wsum = w.sum(axis=1, keepdims=True)
    mean = (rgb * w[..., None]).sum(axis=1) / wsum
    d = rgb - mean[:, None, :]
    cov = np.einsum('nki,nkj,nk->nij', d, d, w)
    axis = np.ones((n, 3), dtype=np.float32)
    for _ in range(8):                                  # iteration de puissance : axe principal
        axis = np.einsum('nij,nj->ni', cov, axis)
        norm = np.linalg.norm(axis, axis=1, keepdims=True)
        axis = np.where(norm > 1e-6, axis / np.maximum(norm, 1e-6), np.array([0.577, 0.577, 0.577], dtype=np.float32))
    t = np.einsum('nki,ni->nk', d, axis)
    big = np.where(w > 0, t, -np.inf).max(axis=1)
    small = np.where(w > 0, t, np.inf).min(axis=1)
    e0 = np.clip(mean + big[:, None] * axis, 0, 255)
    e1 = np.clip(mean + small[:, None] * axis, 0, 255)
    c0, c1, idx, err = _assigner(rgb, w, e0, e1)
    # affinage : extremites recalculees par moindres carres a partir des indices, gardees si l'erreur baisse
    coef = np.array([1.0, 0.0, 2 / 3, 1 / 3], dtype=np.float32)
    for _ in range(2):
        p0, p1 = _565_rgb(c0), _565_rgb(c1)
        ka = coef[idx] * w                                # poids de l'extremite 0 (couleur0 apres echange)
        kb = (1 - coef[idx]) * w
        aa, bb, ab = (coef[idx] * ka).sum(1), ((1 - coef[idx]) * kb).sum(1), (coef[idx] * kb).sum(1)
        det = aa * bb - ab * ab
        ax = (ka[..., None] * rgb).sum(1)
        bx = (kb[..., None] * rgb).sum(1)
        ok = np.abs(det) > 1e-3
        safe = np.where(ok, det, 1.0)[:, None]
        n0 = np.clip((bb[:, None] * ax - ab[:, None] * bx) / safe, 0, 255)
        n1 = np.clip((aa[:, None] * bx - ab[:, None] * ax) / safe, 0, 255)
        n0 = np.where(ok[:, None], n0, p0)
        n1 = np.where(ok[:, None], n1, p1)
        r0, r1, ridx, rerr = _assigner(rgb, w, n0, n1)
        better = rerr < err
        c0, c1 = np.where(better, r0, c0), np.where(better, r1, c1)
        idx = np.where(better[:, None], ridx, idx)
        err = np.where(better, rerr, err)
    bits = np.zeros(n, dtype=np.uint32)
    for i in range(16):
        bits |= idx[:, i] << np.uint32(2 * i)
    out = np.zeros((n, 8), dtype=np.uint8)
    out[:, 0:2] = np.stack([c0 & 0xff, c0 >> 8], axis=1).astype(np.uint8)
    out[:, 2:4] = np.stack([c1 & 0xff, c1 >> 8], axis=1).astype(np.uint8)
    for k in range(4):
        out[:, 4 + k] = ((bits >> np.uint32(8 * k)) & np.uint32(0xff)).astype(np.uint8)
    return out


def encoder_bc3(rgba: np.ndarray) -> bytes:
    b = _blocs(rgba)
    nby, nbx = b.shape[:2]
    flat = b.reshape(nby * nbx, 16, 4)
    alpha = _alpha_bloc(flat[:, :, 3])
    # couleurs ponderees par l'alpha : un pixel presque transparent compte peu a l'ecran (mesure : +1 a +12 dB sur
    # les niveaux reduits par rapport a un poids 0 / 1)
    color = _couleur_bloc(flat[:, :, :3], flat[:, :, 3] / 255.0)
    return np.concatenate([alpha, color], axis=1).tobytes()


def decoder_bc3(body: bytes, w: int, h: int) -> np.ndarray:
    """Decodage de reference d'un niveau BC3 (w x h, blocs incomplets en bord compris) -> RGBA uint8 (h, w, 4)."""
    nbx, nby = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    blk = np.frombuffer(body, dtype=np.uint8, count=16 * nbx * nby).reshape(nbx * nby, 16)
    a0, a1 = blk[:, 0].astype(np.float32), blk[:, 1].astype(np.float32)
    seven = [a0, a1] + [((7 - k) * a0 + k * a1) / 7 for k in range(1, 7)]
    five = [a0, a1] + [((5 - k) * a0 + k * a1) / 5 for k in range(1, 5)] + [np.zeros_like(a0), np.full_like(a0, 255)]
    apal = np.where((a0 > a1)[:, None], np.stack(seven, 1), np.stack(five, 1))                # (n, 8)
    abits = np.zeros(len(blk), dtype=np.uint64)
    for k in range(6):
        abits |= blk[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    aidx = np.stack([(abits >> np.uint64(3 * i)) & np.uint64(7) for i in range(16)], 1).astype(np.int64)
    alpha = np.take_along_axis(apal, aidx, 1)
    c0 = blk[:, 8].astype(np.uint32) | (blk[:, 9].astype(np.uint32) << 8)
    c1 = blk[:, 10].astype(np.uint32) | (blk[:, 11].astype(np.uint32) << 8)
    p0, p1 = _565_rgb(c0), _565_rgb(c1)
    cpal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)                      # (n, 4, 3)
    cb = blk[:, 12:16].astype(np.uint32)
    cbits = cb[:, 0] | (cb[:, 1] << 8) | (cb[:, 2] << 16) | (cb[:, 3] << 24)
    cidx = np.stack([(cbits >> np.uint32(2 * i)) & np.uint32(3) for i in range(16)], 1).astype(np.int64)
    rgb = np.take_along_axis(cpal, cidx[..., None], 1)                                       # (n, 16, 3)
    px = np.clip(np.round(np.concatenate([rgb, alpha[..., None]], axis=2)), 0, 255).astype(np.uint8)
    img = px.reshape(nby, nbx, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(nby * 4, nbx * 4, 4)
    return img[:h, :w].copy()


def decoder(data: bytes) -> List[np.ndarray]:
    """Tous les niveaux d'une icone BC3 par le decodage de reference (ce que le jeu affiche)."""
    f = forme(data)
    if f.format != BC3_UNORM:
        raise EIcone('format DXGI {} : seul BC3_UNORM (77) est decode ici'.format(f.format))
    out, pos = [], f.taille_entete
    for w, h in tailles_niveaux(f.largeur, f.hauteur, f.niveaux):
        out.append(decoder_bc3(data[pos:pos + taille_bc3(w, h)], w, h))
        pos += taille_bc3(w, h)
    return out


# ---------------------------------------------------------------------------
# Fabrication et controle
# ---------------------------------------------------------------------------

def psnr(a: np.ndarray, b: np.ndarray, mask=None) -> float:
    a, b = a.astype(np.float64), b.astype(np.float64)
    if mask is not None:
        if not mask.any():
            return math.inf
        a, b = a[mask], b[mask]
    mse = ((a - b) ** 2).mean()
    return math.inf if mse == 0 else 10 * math.log10(255.0 ** 2 / mse)


def fabriquer(image, modele: bytes) -> Tuple[bytes, List[str]]:
    """Image (chemin ou PIL) -> .ddsc de meme forme que le modele (une icone du jeu). Renvoie (octets, rapport)."""
    f = forme(modele)
    if f.format != BC3_UNORM:
        raise EIcone('modele au format DXGI {} : seul BC3_UNORM (77, celui des icones) est pris en charge'.format(f.format))
    sizes = tailles_niveaux(f.largeur, f.hauteur, f.niveaux)
    if sum(taille_bc3(w, h) for w, h in sizes) != f.taille_corps:
        raise EIcone('taille du corps du modele ({}) differente de celle de {} niveaux BC3 de {}x{}'.format(
            f.taille_corps, f.niveaux, f.largeur, f.hauteur))
    rgba, notes = preparer(image, f.largeur, f.hauteur)
    levels = niveaux(rgba, sizes)
    body = b''.join(encoder_bc3(lv) for lv in levels)
    out = modele[:f.taille_entete] + body
    lines = ['icone : {}x{}, BC3, {} niveaux, en-tete du modele'.format(f.largeur, f.hauteur, f.niveaux)] + \
            ['  ' + n for n in notes]
    lines += controler(out, modele, levels)
    return out, lines


def controler(data: bytes, modele: bytes, attendu: List[np.ndarray]) -> List[str]:
    """Meme forme et meme en-tete que le modele ; chaque niveau, par le decodage de reference, proche de l'image
    voulue ; lecture par deca.ff_avtx concordante."""
    if data[:HEADER_SIZE] != modele[:HEADER_SIZE] or forme(data) != forme(modele):
        raise EIcone('controle : en-tete ou forme differents du modele')
    refs, lus = decoder(data), lire(data)
    worst = []
    for k, (ref, got, want) in enumerate(zip(refs, lus, attendu)):
        h, w = want.shape[:2]
        if min(h, w) < COTE_MIN_CONTROLE:
            continue                                    # 1 a 3 blocs : quelques pixels, PSNR sans signification
        # deca.dxgi ne decode que les blocs entiers (largeur // 4) : concordance sur la zone qu'il decode
        hh, ww = (h // 4) * 4, (w // 4) * 4
        diff = np.abs(got[:hh, :ww].astype(np.int16) - ref[:hh, :ww].astype(np.int16))
        if diff[..., :3].max() > ECART_DECA_COULEUR or diff[..., 3].max() > ECART_DECA_ALPHA:
            raise EIcone('controle : niveau {} ({}x{}) lu autrement par DECA (ecart {} en couleur, {} en alpha) : '
                         'erreur d\'encodage'.format(k, w, h, diff[..., :3].max(), diff[..., 3].max()))
        pc = psnr(ref[..., :3], want[..., :3], want[..., 3] >= 128)
        pa = psnr(ref[..., 3], want[..., 3])
        worst.append((k, pc, pa))
        seuil = PSNR_MIN if k == 0 and min(h, w) >= COTE_MIN_NIVEAU_0 else PSNR_MIN_NIVEAUX
        if pc < seuil or pa < seuil:
            raise EIcone('controle : niveau {} ({}x{}) trop eloigne de l\'image (PSNR couleurs {:.1f} dB, alpha {:.1f} dB)'.format(
                k, w, h, pc, pa))
    k, pc, pa = worst[0]
    low = min(x[1] for x in worst[1:]) if len(worst) > 1 else pc
    return ['  controle : niveau 0 a {:.1f} dB (couleurs visibles), {:.1f} dB (alpha) ; niveaux 1 a {} >= {:.1f} dB ; '
            'lecture DECA concordante'.format(pc, pa, worst[-1][0], low)]


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _projet  # noqa: F401  (substitut de zugbruecke hors Windows)
    ap = argparse.ArgumentParser(description='Icone d\'objet (.ddsc) : lecture, fabrication depuis une image.')
    sub = ap.add_subparsers(dest='commande', required=True)
    lp = sub.add_parser('lire', help='forme d\'une icone, et export PNG')
    lp.add_argument('ddsc')
    lp.add_argument('--png', help='ecrit le plus grand niveau en PNG')
    fp = sub.add_parser('fabriquer', help='image -> .ddsc de meme forme qu\'un modele')
    fp.add_argument('image')
    fp.add_argument('--modele', required=True, help='icone du jeu (.ddsc) dont reprendre la forme et l\'en-tete')
    fp.add_argument('--sortie', required=True)
    args = ap.parse_args(argv)
    try:
        if args.commande == 'lire':
            data = Path(args.ddsc).read_bytes()
            f = forme(data)
            print('{} : {}x{}, format DXGI {}, {} niveaux, en-tete {} + corps {} octets'.format(
                args.ddsc, f.largeur, f.hauteur, f.format, f.niveaux, f.taille_entete, f.taille_corps))
            if args.png:
                from PIL import Image
                Image.fromarray(lire(data)[0], 'RGBA').save(args.png)
                print('Ecrit : {}'.format(args.png))
            return 0
        out, lines = fabriquer(args.image, Path(args.modele).read_bytes())
    except EIcone as e:
        raise SystemExit('ERREUR : {}'.format(e))
    Path(args.sortie).write_bytes(out)
    print('\n'.join(lines + ['Ecrit : {} ({} octets)'.format(args.sortie, len(out))]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
