"""
*.ttfc of theHunter: COTW (textures/qui/droidsans.ttfc): a plain TrueType / OpenType font, no APEX header.

COTWed DECA, 2026-10-09. The reader checks the table directory (big endian, as in every sfnt file) and decodes
the 'name' table, enough to say what the font is and to export it as .ttf.
"""
import struct
from typing import Dict, List, Tuple

SFNT_VERSIONS = (b'\x00\x01\x00\x00', b'OTTO', b'true')
NAME_IDS = {0: 'copyright', 1: 'family', 2: 'subfamily', 3: 'unique id', 4: 'full name', 5: 'version',
            6: 'PostScript name', 7: 'trademark', 8: 'manufacturer', 9: 'designer', 11: 'vendor URL',
            13: 'license', 14: 'license URL'}


def tables(data: bytes) -> List[Tuple[str, int, int, int]]:
    """Table directory: (tag, checksum, offset, length). ValueError if it is not an sfnt font."""
    if len(data) < 12 or data[:4] not in SFNT_VERSIONS:
        raise ValueError('not an sfnt font (version {!r})'.format(data[:4]))
    num = struct.unpack_from('>H', data, 4)[0]
    if not 1 <= num <= 64 or 12 + 16 * num > len(data):
        raise ValueError('sfnt: {} tables announced for {} bytes'.format(num, len(data)))
    out = []
    for i in range(num):
        tag, checksum, offset, length = struct.unpack_from('>4sIII', data, 12 + 16 * i)
        if offset + length > len(data):
            raise ValueError('sfnt: table {!r} ends at {} beyond the file ({})'.format(tag, offset + length, len(data)))
        out.append((tag.decode('latin-1'), checksum, offset, length))
    return out


def is_ttf(data: bytes) -> bool:
    try:
        tables(data)
    except (ValueError, struct.error):
        return False
    return True


def is_ttf_header(size: int, head: bytes) -> bool:
    """Test on the first bytes and the file size (ff_determine reads 256 bytes): directory inside the file."""
    if len(head) < 12 or head[:4] not in SFNT_VERSIONS:
        return False
    num = struct.unpack_from('>H', head, 4)[0]
    if not 1 <= num <= 64 or 12 + 16 * num > size:
        return False
    for i in range(min(num, (len(head) - 12) // 16)):
        _tag, _c, offset, length = struct.unpack_from('>4sIII', head, 12 + 16 * i)
        if offset + length > size:
            return False
    return True


def names(data: bytes) -> Dict[str, str]:
    """name table: name id -> text (Windows Unicode records first, then Macintosh Roman)."""
    found = {t: (o, n) for t, _c, o, n in tables(data)}
    if 'name' not in found:
        return {}
    base, _length = found['name']
    _fmt, count, string_offset = struct.unpack_from('>HHH', data, base)
    out: Dict[str, str] = {}
    records = [struct.unpack_from('>HHHHHH', data, base + 6 + 12 * i) for i in range(count)]
    for platform, _enc, _lang, name_id, length, offset in sorted(records, key=lambda r: r[0] != 3):
        label = NAME_IDS.get(name_id)
        if label is None or label in out:
            continue
        raw = data[base + string_offset + offset:base + string_offset + offset + length]
        out[label] = raw.decode('utf-16-be', 'replace') if platform in (0, 3) else raw.decode('mac_roman', 'replace')
    return out


def dump_lines(data: bytes) -> List[str]:
    t = tables(data)
    lines = ['Font (sfnt {}), {} bytes, {} tables'.format(
        'TrueType' if data[:4] != b'OTTO' else 'OpenType CFF', len(data), len(t))]
    for label, text in names(data).items():
        lines.append('  {:<16} {}'.format(label, text))
    lines.append('Tables:')
    lines += ['  {:<4} offset 0x{:06x} length {:8d} checksum 0x{:08x}'.format(tag, off, n, c) for tag, c, off, n in t]
    lines.append('Export: the bytes are the .ttf file as is.')
    return lines
