#!/usr/bin/env python3
"""
Tests des decodeurs BC1 a BC5 de DECA (deca/dxgi_bc.py, appeles par deca.dxgi.process_image), corriges le
2026-10-28 (defauts D1 a D4 du journal) ; BC7 : nombre de blocs (D3).

Controles :
  - blocs ecrits a la main, valeurs attendues calculees d'apres la spec. de Direct3D (565 etendu par recopie des
    bits de poids fort, 4 couleurs toujours en BC2 / BC3, arrondis, SNORM) ;
  - un decodeur de reference ecrit ici en numpy flottant, directement d'apres la spec., sur des blocs aleatoires
    (des octets quelconques forment des blocs BC valides) : egalite exacte ;
  - Pillow (decodeur BCn independant : recopie des bits, divisions tronquees) : ecart <= 2 ;
  - cotw/icone.py (decoder_bc3, la reference des icones) : egalite exacte, bords de taille non multiple de 4 compris ;
  - version C (cpp/process_image, si gcc est la) : memes pixels que la version Python.

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_textures_bc.py
"""
import ctypes
import io
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import bc_fabrique as BF  # noqa: E402
import icone as I  # noqa: E402
from deca import dxgi  # noqa: E402
from deca.fast_file import FFError  # noqa: E402

BC1, BC2, BC3, BC4, BC4S, BC5, BC5S, BC7 = 71, 74, 77, 80, 81, 83, 84, 98
BLOCK = {BC1: 8, BC2: 16, BC3: 16, BC4: 8, BC4S: 8, BC5: 16, BC5S: 16, BC7: 16}


def decode(fmt: int, raw: bytes, w: int, h: int) -> np.ndarray:
    """Comme ff_avtx.load_mip : image d'au moins 4 x 4, rognee ici a w x h."""
    img = np.zeros((max(4, h), max(4, w), 4), dtype=np.uint8)
    dxgi.process_image(img, raw, w, h, fmt)
    return img[:h, :w]


def blocks(w: int, h: int) -> int:
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4)


# ---------------------------------------------------------------------------------------------------------
# Decodeur de reference, ecrit d'apres la spec. de Direct3D 11 (flottants, arrondi a la fin)
# ---------------------------------------------------------------------------------------------------------

def ref_565(c: int):
    """UNORM 5 / 6 bits -> [0, 255] en flottant, sans arrondi (comme XMLoadU565 x (1/31, 1/63, 1/31) de DirectXTex)."""
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return np.array([r * 255.0 / 31, g * 255.0 / 63, b * 255.0 / 31])


def ref_color(blk: bytes, four: bool) -> np.ndarray:
    c0, c1, idx = struct.unpack_from('<HHI', blk)
    p0, p1 = ref_565(c0), ref_565(c1)
    if four or c0 > c1:
        pal = [np.append(p0, 255), np.append(p1, 255), np.append((2 * p0 + p1) / 3, 255), np.append((p0 + 2 * p1) / 3, 255)]
    else:
        pal = [np.append(p0, 255), np.append(p1, 255), np.append((p0 + p1) / 2, 255), np.zeros(4)]
    return np.array([pal[(idx >> (2 * p)) & 3] for p in range(16)])


def ref_alpha(blk: bytes, signed: bool) -> np.ndarray:
    a0, a1 = struct.unpack_from('<bb' if signed else '<BB', blk)
    idx = int.from_bytes(blk[2:8], 'little')
    if signed:
        a0, a1 = max(a0, -127), max(a1, -127)
    if a0 > a1:
        pal = [a0, a1] + [((7 - k) * a0 + k * a1) / 7 for k in range(1, 7)]
    else:
        lo, hi = (-127, 127) if signed else (0, 255)
        pal = [a0, a1] + [((5 - k) * a0 + k * a1) / 5 for k in range(1, 5)] + [lo, hi]
    vals = np.array([pal[(idx >> (3 * p)) & 7] for p in range(16)], dtype=np.float64)
    return (vals / 127 + 1) * 127.5 if signed else vals


def ref_decode(fmt: int, raw: bytes, w: int, h: int) -> np.ndarray:
    bnx, bny = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    out = np.zeros((bny * 4, bnx * 4, 4))
    bs = BLOCK[fmt]
    for n in range(bnx * bny):
        blk = raw[n * bs:(n + 1) * bs]
        px = np.zeros((16, 4))
        px[:, 3] = 255
        if fmt == BC1:
            px = ref_color(blk, False)
        elif fmt == BC2:
            px = ref_color(blk[8:], True)
            a = int.from_bytes(blk[:8], 'little')
            px[:, 3] = [((a >> (4 * p)) & 15) * 17 for p in range(16)]
        elif fmt == BC3:
            px = ref_color(blk[8:], True)
            px[:, 3] = ref_alpha(blk, False)
        elif fmt in (BC4, BC4S):
            px[:, 0] = ref_alpha(blk, fmt == BC4S)
        else:
            px[:, 0] = ref_alpha(blk, fmt == BC5S)
            px[:, 1] = ref_alpha(blk[8:], fmt == BC5S)
        by, bx = divmod(n, bnx)
        out[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4] = px.reshape(4, 4, 4)
    return np.floor(out + 0.5).astype(np.uint8)[:h, :w]


# ---------------------------------------------------------------------------------------------------------

def bc1_block(c0: int, c1: int, idx: int) -> bytes:
    return struct.pack('<HHI', c0, c1, idx)


def same_index(k: int, bits: int = 2) -> int:
    return sum(k << (bits * p) for p in range(16))


class TestBlocsALaMain(unittest.TestCase):
    def test_d1_white_is_255(self):
        img = decode(BC1, bc1_block(0xFFFF, 0x0000, 0), 4, 4)
        self.assertTrue((img == 255).all(), img[0, 0])            # ancien decodeur : 248, 252, 248

    def test_bc1_four_colors_rounded(self):
        img = decode(BC1, bc1_block(0xF800, 0x001F, same_index(2)), 4, 4)   # rouge pur, bleu pur
        self.assertEqual(img[0, 0].tolist(), [170, 0, 85, 255])             # (2*255+0)/3 = 170, (0+255)/3 = 85
        img = decode(BC1, bc1_block(3 << 11, 0, 0), 4, 4)                     # r5 = 3 : 3 / 31 x 255 = 24,7
        self.assertEqual(int(img[0, 0, 0]), 25)                              # recopie des bits : 24 ; ancien : 24

    def test_bc1_three_colors_and_transparent(self):
        img = decode(BC1, bc1_block(0x0000, 0xFFFF, same_index(2)), 4, 4)
        self.assertEqual(img[0, 0].tolist(), [128, 128, 128, 255])          # (0 + 255) / 2 arrondi
        img = decode(BC1, bc1_block(0x0000, 0xFFFF, same_index(3)), 4, 4)
        self.assertEqual(img[0, 0].tolist(), [0, 0, 0, 0])

    def test_d2_bc3_color_block_always_four_colors(self):
        alpha = bytes([255, 255]) + bytes(6)
        img = decode(BC3, alpha + bc1_block(0x0000, 0xFFFF, same_index(3)), 4, 4)
        self.assertEqual(img[0, 0].tolist(), [170, 170, 170, 255])         # ancien : noir (mode 3 couleurs de BC1)
        img = decode(BC2, bytes([0xFF] * 8) + bc1_block(0x0000, 0xFFFF, same_index(2)), 4, 4)
        self.assertEqual(img[0, 0].tolist(), [85, 85, 85, 255])

    def test_d1_bc2_alpha_4_bits(self):
        alpha = struct.pack('<Q', same_index(0xF, 4) & ((1 << 64) - 1))
        img = decode(BC2, alpha + bc1_block(0xFFFF, 0xFFFF, 0), 4, 4)
        self.assertTrue((img[..., 3] == 255).all())                       # ancien : 240
        alpha = struct.pack('<Q', same_index(0x7, 4))
        self.assertEqual(int(decode(BC2, alpha + bc1_block(0, 0, 0), 4, 4)[0, 0, 3]), 7 * 17)

    def test_d4_alpha_interpolation_rounded(self):
        idx = same_index(2, 3).to_bytes(6, 'little')                        # code 2 : (6 a0 + a1) / 7
        img = decode(BC3, bytes([255, 0]) + idx + bc1_block(0, 0, 0), 4, 4)
        self.assertEqual(int(img[0, 0, 3]), 219)                            # 218,57 arrondi ; ancien : 218
        img = decode(BC4, bytes([0, 255]) + same_index(5, 3).to_bytes(6, 'little'), 4, 4)  # mode 5 valeurs, (a0 + 4 a1) / 5
        self.assertEqual(int(img[0, 0, 0]), 204)
        img = decode(BC4, bytes([0, 255]) + same_index(7, 3).to_bytes(6, 'little'), 4, 4)
        self.assertEqual(img[0, 0].tolist(), [255, 0, 0, 255])

    def test_snorm(self):
        blk = bytes([0x7F, 0x81]) + (same_index(0, 3) | (1 << 3) | (7 << 6)).to_bytes(6, 'little')
        img = decode(BC4S, blk, 4, 4)
        self.assertEqual(int(img[0, 0, 0]), 255)                            # +1
        self.assertEqual(int(img[0, 1, 0]), 0)                              # -1
        self.assertEqual(int(img[0, 2, 0]), 36)                             # (127 - 6 x 127) / 7 / 127 -> 36
        zero = bytes([0, 0]) + bytes(6)                                     # 0 signe -> milieu de l'echelle
        img = decode(BC5S, zero + zero, 4, 4)
        self.assertEqual(img[0, 0].tolist(), [128, 128, 0, 255])
        self.assertEqual(decode(BC5, zero + zero, 4, 4)[0, 0].tolist(), [0, 0, 0, 255])  # UNORM : 0

    def test_d3_width_not_multiple_of_4(self):
        """10 x 5 : 3 x 2 blocs ; chaque bloc d'une couleur unie differente."""
        cols = [0xF800, 0x07E0, 0x001F, 0xFFE0, 0x07FF, 0xF81F]
        raw = b''.join(bc1_block(c, 0, 0) for c in cols)
        img = decode(BC1, raw, 10, 5)
        self.assertEqual(img.shape, (5, 10, 4))
        self.assertEqual(img[0, 9].tolist(), [0, 0, 255, 255])              # 3e bloc de la 1re ligne
        self.assertEqual(img[4, 0].tolist(), [255, 255, 0, 255])            # 1er bloc de la 2e ligne
        self.assertEqual(img[4, 9].tolist(), [255, 0, 255, 255])

    def test_small_levels_2x1_and_1x1(self):
        for w, h in ((2, 1), (1, 1), (1, 2)):
            img = decode(BC3, bytes([255, 255]) + bytes(6) + bc1_block(0xFFFF, 0, 0), w, h)
            self.assertTrue((img == 255).all(), (w, h))

    def test_short_buffer_refused(self):
        for fmt in (BC1, BC2, BC3, BC4, BC5, BC7):
            with self.assertRaises(FFError, msg=fmt):
                decode(fmt, bytes(BLOCK[fmt] * 6 - 1), 10, 5)              # 3 x 2 blocs attendus


class TestReference(unittest.TestCase):
    SIZES = [(4, 4), (8, 8), (16, 8), (10, 5), (6, 6), (2, 1), (1, 1), (20, 12), (7, 3)]

    def test_random_blocks_equal_spec_reference(self):
        rng = np.random.default_rng(1)
        for fmt in (BC1, BC2, BC3, BC4, BC4S, BC5, BC5S):
            for w, h in self.SIZES:
                raw = rng.integers(0, 256, BLOCK[fmt] * blocks(w, h), dtype=np.uint8).tobytes()
                got, want = decode(fmt, raw, w, h), ref_decode(fmt, raw, w, h)
                diff = np.abs(got.astype(int) - want.astype(int)).max()
                self.assertEqual(diff, 0, 'format {} {}x{} : ecart {}'.format(fmt, w, h, diff))

    def test_icone_reference_bc3(self):
        """cotw/icone.py (reference des icones, jusqu'ici a 7 pres de DECA) : egalite exacte, niveaux 10 x 5 et 2 x 1 compris."""
        rgba = BF.image(40, 20, seed=3)
        rgba[..., 3] = np.linspace(0, 255, 40, dtype=np.uint8)[None, :]
        for lvl in I.niveaux(rgba, [(40, 20), (20, 10), (10, 5), (5, 2), (2, 1), (1, 1)]):
            h, w = lvl.shape[:2]
            body = I.encoder_bc3(lvl)
            self.assertEqual(np.abs(decode(BC3, body, w, h).astype(int) - I.decoder_bc3(body, w, h).astype(int)).max(), 0,
                             (w, h))

    def test_pillow_within_one(self):
        """Pillow (BcnDecode.c : 565 etendu par recopie des bits de poids fort, divisions tronquees) : ecart <= 2."""
        from PIL import Image
        rng = np.random.default_rng(2)
        w, h = 16, 8
        for fmt, fourcc in ((BC1, b'DXT1'), (BC2, b'DXT3'), (BC3, b'DXT5')):
            raw = rng.integers(0, 256, BLOCK[fmt] * blocks(w, h), dtype=np.uint8).tobytes()
            # DDS_HEADER : taille, drapeaux, h, w, taille lineaire, profondeur, niveaux, 11 reserves ; DDS_PIXELFORMAT :
            # taille 32, DDPF_FOURCC, FourCC, 5 x 0 ; caps (TEXTURE), caps2 a 4, reserve
            hdr = struct.pack('<4s7I44s2I4s5I5I', b'DDS ', 124, 0x81007, h, w, len(raw), 0, 1, bytes(44),
                              32, 4, fourcc, 0, 0, 0, 0, 0, 0x1000, 0, 0, 0, 0)
            pil = np.asarray(Image.open(io.BytesIO(hdr + raw)).convert('RGBA'))
            diff = np.abs(decode(fmt, raw, w, h).astype(int) - pil.astype(int)).max()
            self.assertLessEqual(diff, 2, 'format {} : ecart {} avec Pillow'.format(fmt, diff))


class TestBc7Blocs(unittest.TestCase):
    def test_bc7_width_not_multiple_of_4(self):
        """Mode 6 (un sous-ensemble, RGBA 7 bits + bit p) uni : chaque bloc d'une couleur ; 10 x 5 = 3 x 2 blocs."""
        def mode6(r, g, b):
            bits, n = 1 << 6, 7                       # mode 6 : bit 6 a 1
            for v in (r, r, g, g, b, b, 127, 127):    # R0 R1 G0 G1 B0 B1 A0 A1 sur 7 bits
                bits |= (v & 0x7F) << n
                n += 7
            bits |= 1 << n                            # p0 = 1, p1 = 1 : 7 bits + p -> 8 bits
            bits |= 1 << (n + 1)
            return bits.to_bytes(16, 'little')        # indices nuls
        cols = [(127, 0, 0), (0, 127, 0), (0, 0, 127), (127, 127, 0), (0, 127, 127), (127, 0, 127)]
        img = decode(BC7, b''.join(mode6(*c) for c in cols), 10, 5)
        self.assertEqual(img[0, 9].tolist(), [1, 1, 255, 255])
        self.assertEqual(img[4, 0].tolist(), [255, 255, 1, 255])
        self.assertEqual(img[4, 9].tolist(), [255, 1, 255, 255])


C_SOURCE = Path(__file__).resolve().parents[2] / 'cpp' / 'process_image' / 'src' / 'process_image.c'


@unittest.skipUnless(shutil.which('gcc'), 'gcc absent : version C non testee')
class TestVersionC(unittest.TestCase):
    """cpp/process_image (process_image.dll / .so, facultatif) : memes pixels que la version Python."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        lib = Path(cls.tmp.name) / 'process_image.so'
        subprocess.run(['gcc', '-fPIC', '-shared', '-O2', str(C_SOURCE), '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.process_image.restype = ctypes.c_int
        cls.lib.process_image.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint32, ctypes.c_char_p,
                                          ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def c_decode(self, fmt, raw, w, h):
        img = np.zeros((max(4, h), max(4, w), 4), dtype=np.uint8)
        ret = self.lib.process_image(img.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)), len(img), raw, len(raw), w, h, fmt)
        return ret, img[:h, :w]

    def test_same_pixels_as_python(self):
        rng = np.random.default_rng(4)
        for fmt in (BC1, BC2, BC3, BC4, BC5, 70, 72, 76, 79, 82):
            bs = BLOCK.get(fmt, {70: 8, 72: 8, 76: 16, 79: 8, 82: 16}.get(fmt))
            for w, h in TestReference.SIZES:
                raw = rng.integers(0, 256, bs * blocks(w, h), dtype=np.uint8).tobytes()
                ret, got = self.c_decode(fmt, raw, w, h)
                self.assertEqual(ret, 0)
                self.assertTrue(np.array_equal(got, decode(fmt, raw, w, h)), (fmt, w, h))

    def test_uncompressed_small_levels(self):
        rng = np.random.default_rng(5)
        for fmt in (28, 87):
            for w, h in ((2, 1), (3, 3), (5, 2)):
                raw = rng.integers(0, 256, 4 * w * h, dtype=np.uint8).tobytes()
                ret, got = self.c_decode(fmt, raw, w, h)
                self.assertEqual(ret, 0)
                self.assertTrue(np.array_equal(got, decode(fmt, raw, w, h)), (fmt, w, h))

    def test_short_data_and_python_formats(self):
        self.assertEqual(self.c_decode(BC1, bytes(47), 10, 5)[0], -3)
        self.assertEqual(self.c_decode(BC4S, bytes(8), 4, 4)[0], -1)   # SNORM, BC7... : version Python
        self.assertEqual(self.c_decode(BC7, bytes(16), 4, 4)[0], -1)


if __name__ == '__main__':
    unittest.main()
