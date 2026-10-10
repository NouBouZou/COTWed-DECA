"""
*.fmod_guids of theHunter: COTW: the FMOD Studio banks and events an entity or a location uses.

COTWed DECA, 2026-10-09, from four weapon entities (456 to 824 bytes) and the 8-byte files of most locations;
2026-10-10, from five global_sound_layers_<reserve>.fmod_guids (757 to 1 046 bytes), which also list banks:

    u32 version (2)
    u16 bank count B (0 in 5 684 of the 5 707 files; 3 to 22 in the 23 global_sound_layers files)
    B x (u16 length, bank name: 'animal_boar', 'music_germanfarmlands', 'reserve_germanfarmlands'; the last part of
         a 'bank:/...' path of the string bank, so the file sound/fmod_banks/<name>.fmod_bankc)
    u16 GUID count
    GUID count x GUID (16 bytes, Windows layout: u32, u16, u16, 8 bytes; FMOD prints them as {xxxxxxxx-...})

Every sample is read to its last byte. 3 518 of the 5 707 files are empty (no bank, no GUID: 8 bytes).
The event paths ("event:/...") of the GUIDs are in the string bank sound/fmod_banks/master_strings.fmod_bankc
(STDT chunk, ff_fmod_bank.py).
"""
import struct
import uuid
from typing import List, Optional, Sequence, Tuple

VERSION = 2


MAX_SIZE = 1 << 20      # the largest COTW file has 824 bytes


def parse(data: bytes) -> Tuple[int, List[str], List[uuid.UUID]]:
    """(version, bank names, GUIDs); ValueError if the bytes are not a fmod_guids file (read to the last byte)."""
    if len(data) < 8:
        raise ValueError('fmod_guids: {} bytes, the smallest file has 8'.format(len(data)))
    version, nbanks = struct.unpack_from('<IH', data, 0)
    if version != VERSION:
        raise ValueError('fmod_guids: version {}, expected {}'.format(version, VERSION))
    p, banks = 6, []
    for i in range(nbanks):
        if p + 2 > len(data):
            raise ValueError('fmod_guids: bank name {} beyond the end'.format(i))
        n = struct.unpack_from('<H', data, p)[0]
        raw = bytes(data[p + 2:p + 2 + n])
        if len(raw) != n or not raw or not all(32 < c < 127 for c in raw):
            raise ValueError('fmod_guids: bank name {} is not a name'.format(i))
        banks.append(raw.decode('ascii'))
        p += 2 + n
    if p + 2 > len(data):
        raise ValueError('fmod_guids: no GUID count')
    count = struct.unpack_from('<H', data, p)[0]
    p += 2
    if len(data) != p + 16 * count:
        raise ValueError('fmod_guids: {} GUIDs announced from 0x{:x}, size {} (expected {})'.format(
            count, p, len(data), p + 16 * count))
    return version, banks, [uuid.UUID(bytes_le=bytes(data[p + 16 * i:p + 16 * i + 16])) for i in range(count)]


def is_fmod_guids(size: int, header: bytes) -> bool:
    """Structural test on the size and the first 8 bytes (ff_determine), for a file without bank names."""
    if size < 8 or len(header) < 8:
        return False
    version, nbanks, count = struct.unpack_from('<IHH', header, 0)
    return version == VERSION and nbanks == 0 and size == 8 + 16 * count


def may_have_banks(size: int, header: bytes) -> bool:
    """Header of a file with bank names (ff_determine then reads it whole and calls is_fmod_guids_data)."""
    if not 10 <= size <= MAX_SIZE or len(header) < 8:
        return False
    version, nbanks, first = struct.unpack_from('<IHH', header, 0)
    return version == VERSION and 0 < nbanks < 4096 and 0 < first < 256


def is_fmod_guids_data(data: bytes) -> bool:
    try:
        parse(data)
    except ValueError:
        return False
    return True


def to_bytes(guids: List[uuid.UUID], version: int = VERSION, banks: Sequence[str] = ()) -> bytes:
    out = struct.pack('<IH', version, len(banks))
    for b in banks:
        raw = b.encode('ascii')
        out += struct.pack('<H', len(raw)) + raw
    return out + struct.pack('<H', len(guids)) + b''.join(g.bytes_le for g in guids)


def dump_lines(data: bytes, names: Optional[dict] = None) -> List[str]:
    """Text of the Structure viewer. names: GUID -> FMOD path, when known (events, and the bank paths)."""
    version, banks, guids = parse(data)
    names = names or {}
    lines = ['FMOD event GUIDs: {} (version {}{})'.format(
        len(guids), version, ', {} bank(s)'.format(len(banks)) if banks else '')]
    if banks:
        paths = {p.rsplit('/', 1)[-1]: p for p in names.values() if isinstance(p, str) and p.startswith('bank:/')}
        lines.append('banks:')
        lines += ['  {}{}'.format(b, '  ' + paths[b] if b in paths else '') for b in banks]
        lines.append('events:')
    for i, g in enumerate(guids):
        name = names.get(g)
        lines.append('[{:3d}] {{{}}}{}'.format(i, str(g), '  ' + name if name else ''))
    if not guids and not banks:
        lines.append('(empty list: no FMOD event)')
    return lines
