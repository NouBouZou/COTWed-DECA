"""
OBC files: (u32) version, (u32) count, then count fixed-size records of f32.

- version 4 (DECA, other APEX games): 80-byte records (20 f32), meaning not established; shown as raw values.
- version 1 (theHunter: COTW, COTWed DECA 2026-10-09, five samples: occluders.obc, lodges, outposts, patch
  locations): 76-byte records = occluder boxes:
      3 f32  half extents (one of them is thin for wall-like occluders: 0.035, 0.05)
      16 f32 world transform, row-major: three rows of rotation with 0, then the translation with 1
             (positions in world metres, e.g. 8196, 1034, 8196 in hp_trophyworld)
"""
import struct
from typing import List, Optional

import numpy as np

RECORD_SIZE = {1: 76, 4: 80}


def obc_version(data_or_size, header: Optional[bytes] = None) -> Optional[int]:
    """Version of an OBC file if its size matches its header exactly, else None.
    obc_version(data) or obc_version(file_size, first_8_bytes)."""
    if header is None:
        data = bytes(data_or_size)
        size, header = len(data), data[:8]
    else:
        size = data_or_size
    if len(header) < 8:
        return None
    ver, cnt = struct.unpack('<II', header[:8])
    rec = RECORD_SIZE.get(ver)
    if rec is not None and size == 8 + rec * cnt:
        return ver
    return None


class Obc:
    def __init__(self):
        self.version = None
        self.table = None

    def deserialize(self, file):
        self.version, count = struct.unpack('<II', file.read(8))
        rec = RECORD_SIZE.get(self.version)
        if rec is None:
            raise ValueError('OBC version {} unknown (known: {})'.format(self.version, sorted(RECORD_SIZE)))
        data = file.read(rec * count)
        if len(data) != rec * count:
            raise ValueError('OBC truncated: {} records announced, {} bytes read'.format(count, len(data)))
        self.table = np.frombuffer(data, dtype=np.dtype('{}f4'.format(rec // 4)))

    def boxes(self) -> List[dict]:
        """Version 1 records as dicts: half_extents, rotation (3 rows), position."""
        if self.version != 1:
            raise ValueError('boxes() reads version 1 only')
        out = []
        for row in self.table:
            v = [float(x) for x in row]
            out.append({'half_extents': v[0:3], 'rotation': [v[3:6], v[7:10], v[11:14]], 'position': v[15:18],
                        'w': [v[6], v[10], v[14], v[18]]})
        return out

    def dump_lines(self) -> List[str]:
        if self.table is None:
            return []
        if self.version == 1:
            lines = ['OBC version 1: {} occluder boxes (half extents, position, rotation rows)'.format(len(self.table))]
            for i, b in enumerate(self.boxes()):
                he, p, r = b['half_extents'], b['position'], b['rotation']
                lines.append('[{:4d}] half {:9.4f} {:9.4f} {:9.4f} | pos {:10.3f} {:10.3f} {:10.3f} | rot {}'.format(
                    i, he[0], he[1], he[2], p[0], p[1], p[2],
                    ' '.join('({:.3f} {:.3f} {:.3f})'.format(*row) for row in r)))
            return lines
        lines = ['OBC version {}: {} records of {} floats (meaning not established)'.format(
            self.version, len(self.table), self.table.shape[-1] if self.table.ndim > 1 else len(self.table.dtype))]
        lines += ['[{:4d}] {}'.format(i, ' '.join('{:.4g}'.format(float(x)) for x in row))
                  for i, row in enumerate(self.table)]
        return lines

    def dump_to_string(self, vfs=None):
        return '\n'.join(self.dump_lines())
