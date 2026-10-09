"""
*.fmod_guids of theHunter: COTW: the FMOD Studio events an entity or a location uses.

COTWed DECA, 2026-10-09, from four weapon entities (456 to 824 bytes) and the 8-byte files of most locations:

    u32 version (2)
    u16 0        (not seen otherwise: a reserved field, or the high half of a u32 count; kept as read)
    u16 count
    count x GUID (16 bytes, Windows layout: u32, u16, u16, 8 bytes; FMOD prints them as {xxxxxxxx-...})

The size is exactly 8 + 16 x count, which ff_determine checks. 3 518 of the 5 707 files are empty (count 0).
The event paths ("event:/...") of these GUIDs are in the string bank sound/fmod_banks/master_strings.fmod_bankc
(STDT chunk, not decoded yet).
"""
import struct
import uuid
from typing import List, Optional, Tuple

VERSION = 2


def parse(data: bytes) -> Tuple[int, int, List[uuid.UUID]]:
    """(version, reserved, GUIDs); ValueError if the bytes are not a fmod_guids file."""
    if len(data) < 8:
        raise ValueError('fmod_guids: {} bytes, the header has 8'.format(len(data)))
    version, reserved, count = struct.unpack_from('<IHH', data, 0)
    if version != VERSION:
        raise ValueError('fmod_guids: version {}, expected {}'.format(version, VERSION))
    if len(data) != 8 + 16 * count:
        raise ValueError('fmod_guids: {} GUIDs announced, size {} (expected {})'.format(count, len(data), 8 + 16 * count))
    return version, reserved, [uuid.UUID(bytes_le=bytes(data[8 + 16 * i:24 + 16 * i])) for i in range(count)]


def is_fmod_guids(size: int, header: bytes) -> bool:
    """Structural test on the size and the first 8 bytes (ff_determine)."""
    if size < 8 or len(header) < 8:
        return False
    version, reserved, count = struct.unpack_from('<IHH', header, 0)
    return version == VERSION and reserved == 0 and size == 8 + 16 * count


def to_bytes(guids: List[uuid.UUID], version: int = VERSION, reserved: int = 0) -> bytes:
    return struct.pack('<IHH', version, reserved, len(guids)) + b''.join(g.bytes_le for g in guids)


def dump_lines(data: bytes, names: Optional[dict] = None) -> List[str]:
    """Text of the Structure viewer. names: GUID -> event path, when known."""
    version, reserved, guids = parse(data)
    lines = ['FMOD event GUIDs: {} (version {}{})'.format(
        len(guids), version, ', reserved field {}'.format(reserved) if reserved else '')]
    for i, g in enumerate(guids):
        name = (names or {}).get(g)
        lines.append('[{:3d}] {{{}}}{}'.format(i, str(g), '  ' + name if name else ''))
    if not guids:
        lines.append('(empty list: no FMOD event)')
    return lines
