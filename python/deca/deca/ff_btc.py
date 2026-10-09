"""
*.btc of theHunter: COTW: compiled AI behaviour trees (ai/bts/**.btc, 127 files, 3 928 nodes).

COTWed DECA, 2026-10-09, from four samples (492 bytes to 494 KB). Structure only: the code block is not decoded.

    header, 12 u32:
      [0] 0x12 (version 18)        [1] 0x30 (header size: the code starts here)
      [2] offset of a 0x30-byte block that ends the code (u16 values, same layout in every sample)
      [3] offset of the parameter table, [4] its size in bytes, [6] its entry count (20 bytes per entry)
      [5] offset of the parameter values (u32 each, [6] of them)
      [7] offset of the source name, [8] its length (then a NUL: the file ends there)
      [9] a hash (not hash32 of the name), [10] unknown, [11] 0xFFFFFFFF
    parameter entry (20 bytes): u32 hash32 of the name, u16 index, u16 (1), u32 offset (step 8), u32 0, u32 type (2)
    source name: "intermediate/ai/bts/<path>.bts"

The code block between 0x30 and [2] holds the bytecode and its constants; its strings (states, debug messages:
"ROAMING FAILED PATH", "ACT_CALL"...) are listed. 'fly_preset' (wild_turkey_flee) is a parameter name.
"""
import re
import struct
from typing import Dict, List, Optional

VERSION = 0x12
HEADER_SIZE = 0x30
_RX_STRING = re.compile(rb'[\x20-\x7e]{4,}(?=\x00)')


class Btc:
    def __init__(self, data: bytes):
        data = bytes(data)
        if not is_btc(len(data), data[:HEADER_SIZE]):
            raise ValueError('not a behaviour tree (.btc) file')
        self.data = data
        self.header = struct.unpack_from('<12I', data, 0)
        h = self.header
        self.name = data[h[7]:h[7] + h[8]].decode('utf-8', 'replace')
        self.params = []
        if h[3] and h[6]:
            for i in range(h[6]):
                name_hash, index, unk, offset, zero, ptype = struct.unpack_from('<IHHIII', data, h[3] + 20 * i)
                value = struct.unpack_from('<I', data, h[5] + 4 * i)[0] if h[5] else None
                self.params.append({'hash': name_hash, 'index': index, 'unk': unk, 'offset': offset, 'zero': zero,
                                    'type': ptype, 'value': value})
        self.code = data[HEADER_SIZE:h[2]]
        self.trailer = data[h[2]:h[2] + 0x30]

    def strings(self) -> List[str]:
        return [m.group(0).decode('ascii') for m in _RX_STRING.finditer(self.code)]

    def dump_lines(self, names: Optional[Dict[int, str]] = None) -> List[str]:
        h = self.header
        lines = ['Behaviour tree (btc version {}): {}'.format(h[0], self.name),
                 'code: {} bytes (bytecode, not decoded); hash 0x{:08x}; field [10] = {}'.format(
                     len(self.code), h[9], h[10]),
                 'parameters: {}'.format(len(self.params))]
        for p in self.params:
            name = (names or {}).get(p['hash'])
            lines.append('  [{}] 0x{:08x} {:<28} value {}  (offset {}, type {})'.format(
                p['index'], p['hash'], name or '?', p['value'], p['offset'], p['type']))
        strs = self.strings()
        lines.append('strings in the code: {}'.format(len(strs)))
        lines += ['  ' + s for s in strs]
        lines.append('end block: ' + self.trailer.hex(' '))
        return lines


def is_btc(size: int, head: bytes) -> bool:
    """Structural test (ff_determine): version, header size, the source name ends the file."""
    if size < HEADER_SIZE or len(head) < HEADER_SIZE:
        return False
    h = struct.unpack_from('<12I', head, 0)
    return (h[0] == VERSION and h[1] == HEADER_SIZE and h[11] == 0xFFFFFFFF and h[7] + h[8] + 1 == size
            and HEADER_SIZE <= h[2] <= h[7] and (h[3] == 0 or h[3] + h[4] <= h[7]))
