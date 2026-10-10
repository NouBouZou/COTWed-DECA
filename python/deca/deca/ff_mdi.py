"""
Model instance lists of theHunter: COTW (DECA type mdi): *.nl.mdic (1 661 files) and *.fl.mdic (1 150), next to the
.pfs, .obc and .blo of a location: which models (.modelc) stand where.

COTWed DECA, 2026-10-10, from 12 samples (188 bytes to 954 KB, 1 to 10 367 instances), every one read to its last
byte with this layout (little endian):

    0x00  'MDI\\0'
    0x04  u32 version (13)
    0x08  u32 model count M
    0x0C  u32 instance count N
    0x10  u32 size S of the model path block (a multiple of 16)
    0x14  u32 [unknown]: 0 in 11 samples, 5 in trailhead_sh_10.nl.mdic (123 instances, 81 models: no unused or
          repeated model, no scaled instance; 61 instances not upright)
    0x18  8 zero bytes
    0x20  M model paths ('models/.../x.modelc'), each NUL-terminated, zero-padded to S bytes
          N transforms: 4x4 f32 matrix, row by row: three axis rows (x, y, z, 0) then the position row (x, y, z, 1)
          N u16 model index (into the M paths)
          N u16 flags (3 in every sample) [meaning unknown]
          N bounding boxes in world space: min (3 f32), max (3 f32)

The two kinds differ only by their content ([hypothesis] nl: near, small props; fl: far, large structures). Every
model path of the samples is already a named file of the project.
"""
import math
import struct
from typing import List, Optional, Sequence, Tuple

MAGIC = b'MDI\x00'
VERSION = 13
HEADER = 0x20


class EMdi(Exception):
    pass


class Instance:
    __slots__ = ('model', 'matrix', 'flags', 'bbox_min', 'bbox_max')

    def __init__(self, model: int, matrix: Tuple[float, ...], flags: int, bbox_min, bbox_max):
        self.model, self.matrix, self.flags = model, matrix, flags
        self.bbox_min, self.bbox_max = tuple(bbox_min), tuple(bbox_max)

    @property
    def position(self) -> Tuple[float, float, float]:
        return self.matrix[12], self.matrix[13], self.matrix[14]

    @property
    def scale(self) -> Tuple[float, float, float]:
        m = self.matrix
        return tuple(math.sqrt(m[4 * r] ** 2 + m[4 * r + 1] ** 2 + m[4 * r + 2] ** 2) for r in range(3))

    @property
    def yaw_degrees(self) -> float:
        """Rotation about the vertical axis (y), from the x axis row; exact only for an upright instance."""
        m = self.matrix
        return math.degrees(math.atan2(-m[2], m[0]))


class Mdi:
    def __init__(self, data: bytes):
        if len(data) < HEADER or data[:4] != MAGIC:
            raise EMdi('not an MDI file')
        self.version, m, n, s, self.unknown = struct.unpack_from('<5I', data, 4)
        self.reserved = data[0x18:HEADER]
        if self.version != VERSION:
            raise EMdi('MDI version {} not handled (13 expected)'.format(self.version))
        expected = HEADER + s + n * (64 + 2 + 2 + 24)
        if expected != len(data):
            raise EMdi('MDI of {} models and {} instances should be {} bytes, file has {}'.format(
                m, n, expected, len(data)))
        block = data[HEADER:HEADER + s]
        names = block.split(b'\0')[:m]
        if len(names) < m or any(not x for x in names):
            raise EMdi('model path block of {} bytes does not hold {} paths'.format(s, m))
        self.models: List[str] = [x.decode('utf-8', 'replace') for x in names]
        p = HEADER + s
        mats = [struct.unpack_from('<16f', data, p + 64 * i) for i in range(n)]
        p += 64 * n
        idx = struct.unpack_from('<{}H'.format(n), data, p)
        p += 2 * n
        flags = struct.unpack_from('<{}H'.format(n), data, p)
        p += 2 * n
        boxes = [struct.unpack_from('<6f', data, p + 24 * i) for i in range(n)]
        self.instances = [Instance(idx[i], mats[i], flags[i], boxes[i][:3], boxes[i][3:]) for i in range(n)]
        bad = [i for i, inst in enumerate(self.instances) if inst.model >= m]
        if bad:
            raise EMdi('instance {} uses model {} of {}'.format(bad[0], self.instances[bad[0]].model, m))
        self.block_size = s


def mdi_bytes(models: Sequence[str], instances: Sequence[Instance], unknown: int = 0,
              reserved: bytes = bytes(8)) -> bytes:
    """MDI file of these models and instances (the inverse of Mdi: mdi_bytes(m.models, m.instances, m.unknown,
    m.reserved) gives back the bytes read)."""
    block = b''.join(x.encode('utf-8') + b'\0' for x in models)
    block += b'\0' * (-len(block) % 16)
    out = bytearray(MAGIC + struct.pack('<5I', VERSION, len(models), len(instances), len(block), unknown))
    out += bytes(reserved).ljust(8, b'\0')[:8]
    out += block
    for inst in instances:
        out += struct.pack('<16f', *inst.matrix)
    out += struct.pack('<{}H'.format(len(instances)), *[i.model for i in instances])
    out += struct.pack('<{}H'.format(len(instances)), *[i.flags for i in instances])
    for inst in instances:
        out += struct.pack('<6f', *(tuple(inst.bbox_min) + tuple(inst.bbox_max)))
    return bytes(out)


def _v(v) -> str:
    return '({})'.format(', '.join('{:.3f}'.format(x) for x in v))


def dump_lines(data: bytes, limit: Optional[int] = 5000) -> List[str]:
    mdi = Mdi(data)
    counts = [0] * len(mdi.models)
    for inst in mdi.instances:
        counts[inst.model] += 1
    flags = sorted({i.flags for i in mdi.instances})
    lines = ['MDI version {}: {} models, {} instances (flags {})'.format(
        mdi.version, len(mdi.models), len(mdi.instances), ', '.join(str(f) for f in flags) or '-')]
    if mdi.unknown or mdi.reserved.strip(b'\0'):
        lines.append('header 0x14: u32 {} [unknown], 0x18: {}'.format(mdi.unknown, mdi.reserved.hex(' ')))
    if mdi.instances:
        lo = [min(i.bbox_min[k] for i in mdi.instances) for k in range(3)]
        hi = [max(i.bbox_max[k] for i in mdi.instances) for k in range(3)]
        lines.append('bounding box {} to {}'.format(_v(lo), _v(hi)))
    lines += ['', 'models:']
    lines += ['  [{:3d}] {:5d} x {}'.format(k, counts[k], name) for k, name in enumerate(mdi.models)]
    lines += ['', 'instances (position, rotation about y, scale; world bounding box):']
    shown = mdi.instances if limit is None else mdi.instances[:limit]
    for k, inst in enumerate(shown):
        sc = inst.scale
        scale = '{:.3f}'.format(sc[0]) if max(sc) - min(sc) < 1e-3 else _v(sc)
        lines.append('  [{:5d}] {:3d} {}  pos {}  yaw {:7.2f}  scale {}{}  box {} {}'.format(
            k, inst.model, mdi.models[inst.model].rsplit('/', 1)[-1], _v(inst.position), inst.yaw_degrees, scale,
            '' if inst.flags == 3 else '  flags {}'.format(inst.flags), _v(inst.bbox_min), _v(inst.bbox_max)))
    if len(shown) < len(mdi.instances):
        lines.append('  ... {} more'.format(len(mdi.instances) - len(shown)))
    return lines
