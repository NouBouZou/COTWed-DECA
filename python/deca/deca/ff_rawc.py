"""
*.rawc of theHunter: COTW: raw terrain arrays without header (16 files, worlds/*/terrain/).

COTWed DECA, 2026-10-09, four samples:
  - global_heightfield.rawc (1 MiB): 512 x 512 f32, heights in metres (0 to 2 431 in world4)
  - water_nvwaveworks_mod.rawc (1 MiB): 1024 x 1024 u8 (NVIDIA WaveWorks modulation)
  - nv_water_cull_mask.rawc (32 KiB): 512 x 512 bits, most significant bit first

Nothing in the file gives the layout. guess_layout() tries the square layouts that the size allows (u8, u16, f32,
bits) and keeps the smoothest one: neighbouring samples of a real image differ much less than its spread, while
the same bytes read with the wrong element size look like noise. The name breaks ties (heightfield: f32,
mask: bits). The result is labelled as a guess in the viewer.
"""
import math
from typing import List, Optional

import numpy as np


class RawLayout:
    def __init__(self, kind: str, width: int, height: int, array: np.ndarray, score: float):
        self.kind = kind          # 'u8', 'u16', 'f32', 'bits'
        self.width = width
        self.height = height
        self.array = array        # 2D, as decoded
        self.score = score        # smoothness: lower is smoother

    def __repr__(self):
        return 'RawLayout({} {}x{}, score {:.3f})'.format(self.kind, self.width, self.height, self.score)


def _square(n: int) -> Optional[int]:
    r = int(math.isqrt(n)) if n > 0 else 0
    return r if r * r == n and r >= 16 else None


def _plausible_floats(a: np.ndarray) -> bool:
    """Real f32 data stays within a few orders of magnitude. Bytes of a u8 image read as f32 (33 33 33 33 ->
    4.2e-8, next to 5.1e4) span dozens: they are smooth, but not floats."""
    if not np.isfinite(a).all():
        return False
    nz = np.abs(a[a != 0]).astype(np.float64)
    if nz.size == 0:
        return True
    lo, hi = np.percentile(nz, 0.1), np.percentile(nz, 99.9)
    return hi < 1e7 and lo > 1e-12 and hi / lo < 1e9


def candidates(data: bytes) -> List[RawLayout]:
    out = []
    size = len(data)
    side = _square(size)
    if side:
        out.append(('u8', side, np.frombuffer(data, np.uint8).reshape(side, side)))
    if size % 2 == 0 and _square(size // 2):
        s = _square(size // 2)
        out.append(('u16', s, np.frombuffer(data, '<u2').reshape(s, s)))
    if size % 4 == 0 and _square(size // 4):
        s = _square(size // 4)
        a = np.frombuffer(data, '<f4').reshape(s, s)
        if _plausible_floats(a):
            out.append(('f32', s, a))
    if _square(size * 8):
        s = _square(size * 8)
        out.append(('bits', s, np.unpackbits(np.frombuffer(data, np.uint8)).reshape(s, s)))
    layouts = []
    for kind, s, a in out:
        f = a.astype(np.float64)
        spread = f.std()
        if spread == 0:
            score = 0.0
        else:
            score = (np.abs(np.diff(f, axis=0)).mean() + np.abs(np.diff(f, axis=1)).mean()) / (2 * spread)
        layouts.append(RawLayout(kind, s, s, a, float(score)))
    return layouts


def guess_layout(data: bytes, name: str = '') -> Optional[RawLayout]:
    layouts = candidates(data)
    if not layouts:
        return None
    lower = name.lower()
    prefer = 'f32' if 'height' in lower else ('bits' if 'mask' in lower else None)
    layouts.sort(key=lambda l: (round(l.score, 2) if l.kind != prefer else round(l.score, 2) - 0.05, l.kind))
    return layouts[0]


def to_rgba8(layout: RawLayout) -> np.ndarray:
    """Grey levels from the minimum (black) to the maximum (white), RGBA for the image viewer."""
    f = layout.array.astype(np.float64)
    lo, hi = float(f.min()), float(f.max())
    g = np.zeros(f.shape, np.uint8) if hi <= lo else ((f - lo) * (255.0 / (hi - lo))).astype(np.uint8)
    rgba = np.empty(f.shape + (4,), np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = g
    rgba[..., 3] = 255
    return rgba


def describe(layout: RawLayout) -> str:
    f = layout.array
    return '{} {}x{} (guessed, smoothness {:.3f}), min {:.6g}, max {:.6g}'.format(
        layout.kind, layout.width, layout.height, layout.score, float(f.min()), float(f.max()))
