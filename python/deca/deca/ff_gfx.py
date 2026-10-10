"""
Scaleform GFx movies of theHunter: COTW (DECA type gfx): ui/*.gfx, 80 files (menus, HUD, fonts).

COTWed DECA, 2026-10-10, from 5 samples. A GFx file is a Flash SWF with Scaleform's own tags:

    'CFX' (zlib-compressed after the 8-byte header) or 'GFX' (not compressed), u8 SWF version (12 to 43 in COTW),
    u32 length of the uncompressed movie, header included (the zlib data inflates to that length rounded up to
    4 KB with zeros); then the SWF header (frame rectangle, u16 frame rate 8.8, u16 frame count) and the tags:
    u16 (code << 6 | length; length 63: a u32 length follows), up to the End tag (0).

Scaleform tags read here (layouts from DECA's kaitai/gfx.ksy and the samples): 1000 ExporterInfo (u16 version,
u32 flags if version >= 0x10A, u16 bitmap format, u8-length prefix, u8-length movie name), 1001 DefineExternalImage
(u16 id, u16 format, u16 width, u16 height, u8-length file name), 1009 DefineExternalImage2 (u32 id, u16 format,
u16 width, u16 height, u8-length export name, u8-length file name; in COTW the u32 is a u16 image id, the one
DefineSubImage refers to, and a u16 [unknown], 9 in every sample), 1008 DefineSubImage (u16 id, u16 image id,
u16 x1, y1, x2, y2), 71 ImportAssets2 (URL, u8 1, u8 0, u16 symbol count, symbols). An external image 'social_i19.tga' of
ui/social.gfx is the texture ui/social_i19.ddsc (DECA's GFX scan step names them).
ActionScript 3 (DoABC) is shown by name and size only.
"""
import struct
import zlib
from typing import Dict, List, Optional, Tuple

TAG_NAMES = {
    0: 'End', 1: 'ShowFrame', 2: 'DefineShape', 4: 'PlaceObject', 5: 'RemoveObject', 6: 'DefineBits',
    7: 'DefineButton', 8: 'JPEGTables', 9: 'SetBackgroundColor', 10: 'DefineFont', 11: 'DefineText',
    12: 'DoAction', 13: 'DefineFontInfo', 14: 'DefineSound', 15: 'StartSound', 17: 'DefineButtonSound',
    18: 'SoundStreamHead', 19: 'SoundStreamBlock', 20: 'DefineBitsLossless', 21: 'DefineBitsJPEG2',
    22: 'DefineShape2', 23: 'DefineButtonCxform', 24: 'Protect', 26: 'PlaceObject2', 28: 'RemoveObject2',
    32: 'DefineShape3', 33: 'DefineText2', 34: 'DefineButton2', 35: 'DefineBitsJPEG3', 36: 'DefineBitsLossless2',
    37: 'DefineEditText', 39: 'DefineSprite', 43: 'FrameLabel', 45: 'SoundStreamHead2', 46: 'DefineMorphShape',
    48: 'DefineFont2', 56: 'ExportAssets', 57: 'ImportAssets', 58: 'EnableDebugger', 59: 'DoInitAction',
    60: 'DefineVideoStream', 61: 'VideoFrame', 62: 'DefineFontInfo2', 64: 'EnableDebugger2', 65: 'ScriptLimits',
    66: 'SetTabIndex', 69: 'FileAttributes', 70: 'PlaceObject3', 71: 'ImportAssets2', 73: 'DefineFontAlignZones',
    74: 'CSMTextSettings', 75: 'DefineFont3', 76: 'SymbolClass', 77: 'Metadata', 78: 'DefineScalingGrid',
    82: 'DoABC', 83: 'DefineShape4', 84: 'DefineMorphShape2', 86: 'DefineSceneAndFrameLabelData',
    87: 'DefineBinaryData', 88: 'DefineFontName', 89: 'StartSound2', 90: 'DefineBitsJPEG4', 91: 'DefineFont4',
    1000: 'ExporterInfo', 1001: 'DefineExternalImage', 1002: 'FontTextureInfo', 1003: 'DefineExternalGradient',
    1004: 'DefineGradientMap', 1005: 'DefineCompactedFont', 1006: 'DefineExternalSound',
    1007: 'DefineExternalStreamSound', 1008: 'DefineSubImage', 1009: 'DefineExternalImage2',
}


class EGfx(Exception):
    pass


class Tag:
    __slots__ = ('code', 'offset', 'body')

    def __init__(self, code: int, offset: int, body: bytes):
        self.code, self.offset, self.body = code, offset, body

    @property
    def name(self) -> str:
        return TAG_NAMES.get(self.code, 'tag {}'.format(self.code))


def _cstr(b: bytes, p: int) -> Tuple[str, int]:
    q = b.find(b'\0', p)
    if q < 0:
        raise EGfx('string without end at 0x{:x}'.format(p))
    return b[p:q].decode('utf-8', 'replace'), q + 1


def _pstr(b: bytes, p: int) -> Tuple[str, int]:
    n = b[p]
    return b[p + 1:p + 1 + n].decode('utf-8', 'replace'), p + 1 + n


class Gfx:
    def __init__(self, data: bytes):
        if len(data) < 8 or data[:3] not in (b'CFX', b'GFX'):
            raise EGfx('not a GFx file')
        self.compressed = data[:3] == b'CFX'
        self.swf_version = data[3]
        self.length = struct.unpack_from('<I', data, 4)[0]
        if self.compressed:
            try:
                body = zlib.decompress(data[8:])
            except zlib.error as e:
                raise EGfx('zlib: {}'.format(e))
        else:
            body = data[8:]
        if len(body) < self.length - 8:
            raise EGfx('movie of {} bytes, {} after decompression'.format(self.length, len(body) + 8))
        self.body = body
        nbits = body[0] >> 3
        rb = (5 + 4 * nbits + 7) // 8
        bits = int.from_bytes(body[:rb], 'big')
        vals = []
        for k in range(4):
            shift = rb * 8 - 5 - nbits * (k + 1)
            v = (bits >> shift) & ((1 << nbits) - 1)
            if nbits and v >> (nbits - 1):
                v -= 1 << nbits
            vals.append(v)
        self.frame = tuple(v / 20 for v in vals)          # twips: xmin, xmax, ymin, ymax
        self.frame_rate = struct.unpack_from('<H', body, rb)[0] / 256
        self.frame_count = struct.unpack_from('<H', body, rb + 2)[0]
        self.tags: List[Tag] = []
        p, end = rb + 4, self.length - 8
        while p < end:
            if p + 2 > end:
                raise EGfx('tag header cut at 0x{:x}'.format(p + 8))
            h = struct.unpack_from('<H', body, p)[0]
            code, n = h >> 6, h & 0x3F
            start = p + 2
            if n == 0x3F:
                n = struct.unpack_from('<I', body, start)[0]
                start += 4
            if start + n > end:
                raise EGfx('tag {} at 0x{:x} ends beyond the movie'.format(code, p + 8))
            self.tags.append(Tag(code, p + 8, body[start:start + n]))
            p = start + n
            if code == 0:
                break
        if not self.tags or self.tags[-1].code != 0:
            raise EGfx('no End tag')
        if p != end:
            raise EGfx('End tag at 0x{:x}, header says the movie ends at 0x{:x}'.format(p + 8, end + 8))

    def by_code(self, code: int) -> List[Tag]:
        return [t for t in self.tags if t.code == code]

    def exporter(self) -> Optional[Dict]:
        t = next(iter(self.by_code(1000)), None)
        if t is None:
            return None
        b = t.body
        version = struct.unpack_from('<H', b, 0)[0]
        p = 2
        flags = None
        if version >= 0x10A:
            flags = struct.unpack_from('<I', b, p)[0]
            p += 4
        fmt = struct.unpack_from('<H', b, p)[0]
        prefix, p = _pstr(b, p + 2)
        name, p = _pstr(b, p)
        return {'version': version, 'flags': flags, 'bitmap_format': fmt, 'prefix': prefix, 'name': name}

    def external_images(self) -> List[Dict]:
        out = []
        for t in self.tags:
            b = t.body
            if t.code == 1001:
                cid, fmt, w, h = struct.unpack_from('<4H', b, 0)
                name, _ = _pstr(b, 8)
                out.append({'id': cid, 'format': fmt, 'width': w, 'height': h, 'export': '', 'file': name})
            elif t.code == 1009:
                cid, extra, fmt, w, h = struct.unpack_from('<5H', b, 0)
                export, p = _pstr(b, 10)
                name, _ = _pstr(b, p)
                out.append({'id': cid, 'extra': extra, 'format': fmt, 'width': w, 'height': h, 'export': export,
                            'file': name})
        return out

    def sub_images(self) -> List[Tuple[int, int, Tuple[int, int, int, int]]]:
        """(id, image id, (x1, y1, x2, y2)) of the DefineSubImage tags."""
        out = []
        for t in self.by_code(1008):
            cid, image, x1, y1, x2, y2 = struct.unpack_from('<6H', t.body, 0)
            out.append((cid, image, (x1, y1, x2, y2)))
        return out

    def symbols(self, code: int) -> List[Tuple[int, str]]:
        """(character id, name) of SymbolClass (76) or ExportAssets (56) tags."""
        out = []
        for t in self.by_code(code):
            n = struct.unpack_from('<H', t.body, 0)[0]
            p = 2
            for _ in range(n):
                cid = struct.unpack_from('<H', t.body, p)[0]
                name, p = _cstr(t.body, p + 2)
                out.append((cid, name))
        return out

    def imports(self) -> List[Tuple[str, List[str]]]:
        out = []
        for t in self.tags:
            if t.code not in (57, 71):
                continue
            b = t.body
            url, p = _cstr(b, 0)
            if t.code == 71:
                p += 1
                has_digest = b[p]
                p += 1 + (20 if has_digest == 1 else 0)
            n = struct.unpack_from('<H', b, p)[0]
            p += 2
            names = []
            for _ in range(n):
                name, p = _cstr(b, p + 2)
                names.append(name)
            out.append((url, names))
        return out

    def abc_names(self) -> List[Tuple[str, int]]:
        out = []
        for t in self.by_code(82):
            name, p = _cstr(t.body, 4)
            out.append((name, len(t.body) - p))
        return out

    def font_names(self) -> List[str]:
        out = []
        for t in self.tags:
            if t.code in (48, 75) and len(t.body) > 5:
                out.append(_pstr(t.body, 4)[0].rstrip('\0'))
            elif t.code == 88:
                out.append(_cstr(t.body, 2)[0])
        return out

    def frame_labels(self) -> List[str]:
        return [_cstr(t.body, 0)[0] for t in self.by_code(43)]


def dump_lines(data: bytes) -> List[str]:
    g = Gfx(data)
    x0, x1, y0, y1 = g.frame
    lines = ['Scaleform GFx ({}), SWF version {}, movie {} bytes'.format(
        'zlib-compressed' if g.compressed else 'not compressed', g.swf_version, g.length),
        'stage {:g} x {:g}, {:g} frames/s, {} frame(s), {} tags'.format(x1 - x0, y1 - y0, g.frame_rate,
                                                                        g.frame_count, len(g.tags))]
    ex = g.exporter()
    if ex is not None:
        lines.append('exporter: movie "{}", version 0x{:x}, bitmap format {}{}{}'.format(
            ex['name'], ex['version'], ex['bitmap_format'],
            '' if ex['flags'] is None else ', flags 0x{:x}'.format(ex['flags']),
            ', prefix "{}"'.format(ex['prefix']) if ex['prefix'] else ''))
    counts: Dict[str, int] = {}
    for t in g.tags:
        counts[t.name] = counts.get(t.name, 0) + 1
    lines += ['', 'tags:'] + ['  {:5d} x {}'.format(n, k) for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    sections = [
        ('external images (file, size, format, image id, export name)',
         ['{}  {}x{}  format {}  id {}{}{}'.format(
             i['file'], i['width'], i['height'], i['format'], i['id'],
             '' if i.get('extra') is None else ' (u16 {})'.format(i['extra']),
             '  "{}"'.format(i['export']) if i['export'] else '') for i in g.external_images()]),
        ('sub-images (id, image id, rectangle)',
         ['{:5d} image {}  ({}, {}) to ({}, {})'.format(c, im, *r) for c, im, r in g.sub_images()]),
        ('imports', ['{}: {}'.format(url, ', '.join(names) if names else '(no symbol)') for url, names in g.imports()]),
        ('exports', ['{:5d} {}'.format(c, n) for c, n in g.symbols(56)]),
        ('ActionScript classes (SymbolClass)', ['{:5d} {}'.format(c, n) for c, n in g.symbols(76)]),
        ('ActionScript 3 blocks (DoABC)', ['"{}" {} bytes'.format(n, s) for n, s in g.abc_names()]),
        ('fonts', g.font_names()),
        ('frame labels', g.frame_labels()),
    ]
    for title, rows in sections:
        if rows:
            lines += ['', '{}:'.format(title)] + ['  ' + r for r in rows]
    return lines
