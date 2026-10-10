"""
Texture data without header (COTWed DECA, 2026-10-10): the 775 .hmddsc of theHunter: COTW whose .ddsc is not in the
game, and 1 457 unnamed, untyped files of the same kind (2.9 GB).

No file type is given to them: an unnamed file stays untyped, so that a name found later (x.hmddsc) still types it as
DECA does by the extension, and the type control of renommer.py still accepts it. The viewer recognises them on display
(untyped, size of a mip chain: is_bc_raw_size), the audit counts them read with a guessed layout.

A .hmddsc holds the largest mip levels of a texture whose header and small levels are in the .ddsc: block-compressed
data (BC1 to BC7), the smallest of its levels first and the largest last (as deca.ff_avtx.load_atx reads them). Without
the .ddsc, nothing says the format nor the size. Samples (5 orphan .hmddsc, 6 unnamed files):

    2 752 512 bytes = BC1 2048x2048 + 1024x1024 + 512x512, or 16-byte blocks 2048x1024 + ...
    5 570 560 bytes = 16-byte blocks 2048x2048 + 1024 + 512 + 256 (BC5 normal maps, BC3)
    1 376 256 bytes = 16-byte blocks 1024x1024 + 512 + 256, or BC1 2048x1024 + ...

guess() lists the layouts whose mip chain has exactly the file size (power-of-two sides, aspect up to 4:1), decodes a
band of the largest level in each candidate format and ranks them by plausibility: a texture decoded with the right
format and width is smooth (neighbour differences small against the image's spread) and has no seams on the 4-pixel
block grid; a wrong format gives noise, a wrong width gives seams between block rows. score = (mean neighbour
difference / standard deviation) x (difference across block borders / difference inside blocks); lower is better.
On the 11 samples the best guess is the one that looks right (bark BC1 2048x2048, normal map BC5 2048x2048, material
map BC3 1024x1024...). It is a guess: the viewer lists the alternatives.
"""
import math
from typing import List, Sequence, Tuple

import numpy as np

# bytes per 4x4 block -> DXGI formats tried (DECA's decoders: deca.dxgi.process_image). BC7 (98) is tried only when
# nothing else is plausible: its numba decoder takes 12 s to compile, and no COTW sample needed it.
FORMATS = {8: (71, 80), 16: (77, 83)}
BC7 = 98
BC7_ONLY_ABOVE = 0.9
FORMAT_NAMES = {71: 'BC1', 74: 'BC2', 77: 'BC3', 80: 'BC4', 83: 'BC5', 98: 'BC7'}
MAX_ASPECT_LOG2 = 2
MIN_SIZE = 4096
BAND_ROWS = 128


class EBcRaw(Exception):
    pass


def _blocks(w: int, h: int) -> int:
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4)


class Layout:
    """A mip chain: block size, size of the largest level, number of levels in the file."""
    __slots__ = ('block_bytes', 'width', 'height', 'levels')

    def __init__(self, block_bytes: int, width: int, height: int, levels: int):
        self.block_bytes, self.width, self.height, self.levels = block_bytes, width, height, levels

    def level_sizes(self) -> List[Tuple[int, int]]:
        return [(max(1, self.width >> i), max(1, self.height >> i)) for i in range(self.levels)]

    def level_bytes(self, i: int) -> int:
        w, h = self.level_sizes()[i]
        return _blocks(w, h) * self.block_bytes

    def total(self) -> int:
        return sum(self.level_bytes(i) for i in range(self.levels))

    def level_offset(self, i: int) -> int:
        """Offset of level i (0 = largest) in the file: the smaller levels come first."""
        return sum(self.level_bytes(j) for j in range(i + 1, self.levels))

    def __eq__(self, other):
        return isinstance(other, Layout) and (self.block_bytes, self.width, self.height, self.levels) == \
            (other.block_bytes, other.width, other.height, other.levels)

    def __repr__(self):
        return 'Layout({} B/block, {}x{}, {} levels)'.format(self.block_bytes, self.width, self.height, self.levels)


def layouts(size: int) -> List[Layout]:
    """Mip chains of exactly `size` bytes."""
    out = []
    if size < MIN_SIZE:
        return out
    for bpb in (8, 16):
        for lw in range(2, 14):
            for asp in range(-MAX_ASPECT_LOG2, MAX_ASPECT_LOG2 + 1):
                w = 1 << lw
                h = w >> asp if asp > 0 else w << -asp
                if h < 4 or h > 8192:
                    continue
                total = 0
                for k in range(1, 15):
                    total += _blocks(max(1, w >> (k - 1)), max(1, h >> (k - 1))) * bpb
                    if total == size:
                        out.append(Layout(bpb, w, h, k))
                    if total >= size:
                        break
    return out


def is_bc_raw_size(size: int) -> bool:
    return bool(layouts(size))


def _decode(raw: bytes, w: int, h: int, fmt: int) -> np.ndarray:
    import deca.dxgi
    img = np.zeros((max(4, h), max(4, w), 4), dtype=np.uint8)
    deca.dxgi.process_image(img, raw, w, h, fmt)
    return img


def decode_level(data: bytes, layout: Layout, fmt: int, level: int = 0) -> np.ndarray:
    w, h = layout.level_sizes()[level]
    start = layout.level_offset(level)
    return _decode(data[start:start + layout.level_bytes(level)], w, h, fmt)


def plausibility(img: np.ndarray, fmt: int) -> float:
    """Lower is more plausible (see the module docstring). Channels: R for BC4, R and G for BC5, else RGB."""
    channels = {80: (0,), 83: (0, 1)}.get(fmt, (0, 1, 2))
    scores = []
    for c in channels:
        a = img[:, :, c].astype(np.float32)
        if a.shape[0] < 8 or a.shape[1] < 8:
            continue
        dx = np.abs(np.diff(a, axis=1))
        dy = np.abs(np.diff(a, axis=0))
        spread = float(a.std())
        if spread < 0.5:            # flat channel: says nothing
            continue
        tv = (float(dx.mean()) + float(dy.mean())) / 2 / spread
        bx, by = float(dx[:, 3::4].mean()), float(dy[3::4, :].mean())
        ix = float(np.delete(dx, np.s_[3::4], axis=1).mean())
        iy = float(np.delete(dy, np.s_[3::4], axis=0).mean())
        scores.append(tv * (bx + by) / (ix + iy + 1e-3))
    return float(np.mean(scores)) if scores else math.inf


class Guess:
    __slots__ = ('layout', 'fmt', 'score')

    def __init__(self, layout: Layout, fmt: int, score: float):
        self.layout, self.fmt, self.score = layout, fmt, score

    @property
    def format_name(self) -> str:
        return FORMAT_NAMES.get(self.fmt, 'DXGI {}'.format(self.fmt))

    def describe(self) -> str:
        lay = self.layout
        return '{} {}x{}, {} level(s)'.format(self.format_name, lay.width, lay.height, lay.levels)

    def __repr__(self):
        return 'Guess({}, score {:.3f})'.format(self.describe(), self.score)


def guess(data: bytes, band_rows: int = BAND_ROWS) -> List[Guess]:
    """Every (layout, format) whose chain has the file's size, the most plausible first. Scored on a band of rows
    in the middle of the largest level (the whole level when it is small)."""
    out = []
    bands = []
    for lay in layouts(len(data)):
        w, h = lay.width, lay.height
        rows = min(h, band_rows)
        y0 = ((h - rows) // 2) // 4 * 4
        row_bytes = _blocks(w, 4) * lay.block_bytes
        start = lay.level_offset(0) + (y0 // 4) * row_bytes
        raw = data[start:start + (rows // 4) * row_bytes]
        bands.append((lay, raw, rows))
        for fmt in FORMATS[lay.block_bytes]:
            out.append(Guess(lay, fmt, plausibility(_decode(raw, w, rows, fmt), fmt)))
    if not out or min(g.score for g in out) > BC7_ONLY_ABOVE:
        for lay, raw, rows in bands:
            if lay.block_bytes == 16:
                out.append(Guess(lay, BC7, plausibility(_decode(raw, lay.width, rows, BC7), BC7)))
    out.sort(key=lambda g: (g.score, g.layout.levels, g.layout.width != g.layout.height))
    return out


def describe_guesses(guesses: Sequence[Guess], limit: int = 6) -> List[str]:
    return ['{} (score {:.2f})'.format(g.describe(), g.score) for g in guesses[:limit]]


# -- export ----------------------------------------------------------------------------------------------------

def dds_bytes(data: bytes, g: Guess) -> bytes:
    """DDS file (DX10 header) of the guessed texture: its levels, largest first as DDS orders them."""
    import struct
    lay = g.layout
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000       # caps, height, width, pixel format, mips, linear size
    pf = struct.pack('<II4s5I', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    caps = 0x1000 | 0x400000 | 0x8 if lay.levels > 1 else 0x1000
    head = struct.pack('<7I', 124, flags, lay.height, lay.width, lay.level_bytes(0), 0, lay.levels) + bytes(44) + pf
    head += struct.pack('<5I', caps, 0, 0, 0, 0)
    dx10 = struct.pack('<5I', g.fmt, 3, 0, 1, 0)                 # format, TEXTURE2D, flags, array size 1
    body = b''.join(data[lay.level_offset(i):lay.level_offset(i) + lay.level_bytes(i)] for i in range(lay.levels))
    return b'DDS ' + head + dx10 + body


def png_bytes(data: bytes, g: Guess) -> bytes:
    import io
    from PIL import Image
    img = decode_level(data, g.layout, g.fmt)
    if g.fmt in (80, 83):           # one or two channels: opaque
        img[:, :, 3] = 255
    out = io.BytesIO()
    Image.fromarray(img[:g.layout.height, :g.layout.width]).save(out, format='PNG')
    return out.getvalue()


def export_names(base: str, g: Guess) -> Tuple[str, str]:
    """'<base>.DECA.guess_BC1_2048x2048.png' and '.dds': the guess is written in the name."""
    tag = 'DECA.guess_{}_{}x{}'.format(g.format_name, g.layout.width, g.layout.height)
    return '{}.{}.png'.format(base, tag), '{}.{}.dds'.format(base, tag)
