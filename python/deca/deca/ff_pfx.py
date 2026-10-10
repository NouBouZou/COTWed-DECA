"""
Physics files of theHunter: COTW (DECA type pfx): *.pfxc (2 616 files, model physics) and *.pfs (1 743, location
physics), 195 MB.

COTWed DECA, 2026-10-10, from 12 samples: a 0x44-byte header, then a Havok 2014 packfile (ff_havok.py) that runs to
the end of the file:

    0x00  'PFX\\0'
    0x04  u32 1, u32 1, u32 3 [meaning unknown: the same in every sample]
    0x10  52 zero bytes
    0x44  Havok packfile 'hk_2014.1.0-r1': hkRootLevelContainer -> hknpPhysicsSceneData (Havok Physics 2014,
          "hknp"): hknpPhysicsSystemData, shapes (hknpConvexPolytopeShape, hknpStaticCompoundShape, hknpCapsuleShape,
          hknpSphereShape...), mass properties; a .pfxc also has an hkxScene (exporter, source .ma file)
"""
import struct
from typing import List, Tuple

from .ff_havok import MAGIC as HAVOK_MAGIC

MAGIC = b'PFX\x00'
HEADER = 0x44


class EPfx(Exception):
    pass


def header_values(data: bytes) -> Tuple[int, int, int]:
    if len(data) < HEADER + 8 or data[:4] != MAGIC:
        raise EPfx('not a PFX file')
    return struct.unpack_from('<3I', data, 4)


def packfile_bytes(data: bytes) -> bytes:
    """The Havok packfile of a PFX file."""
    header_values(data)
    if data[HEADER:HEADER + 8] != HAVOK_MAGIC:
        raise EPfx('no Havok packfile at 0x{:x}'.format(HEADER))
    return data[HEADER:]


def header_lines(data: bytes) -> List[str]:
    a, b, c = header_values(data)
    rest = data[0x10:HEADER]
    return ['PFX header (0x{:x} bytes): u32 {}, {}, {}{}; Havok packfile from 0x{:x}'.format(
        HEADER, a, b, c, '' if not rest.strip(b'\0') else ', then ' + rest.hex(' '), HEADER)]
