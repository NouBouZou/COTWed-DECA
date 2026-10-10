"""
HumanIK character definitions of theHunter: COTW (DECA type hikcc): animations/humanik/elmer.hikcc, the only one
(7 072 bytes): which skeleton nodes the player character maps to Autodesk HumanIK, with their reference pose.

COTWed DECA, 2026-10-10, read to the last byte:

    0x00  u32 0x24022789 (magic)
    0x04  u32 1 (version [hypothesis])
    0x08  172 x u32 node flags: 1 = node used (53 in elmer.hikcc)
    0x2B8 one record per used node, in node order, 120 bytes: translation (3 f32), rotation quaternion (x, y, z, w),
          scale (3 f32), then 20 f32 all zero in the sample [unknown: limits or pre / post rotations]
          16 zero bytes

Node numbers are those of HumanIK's HIKNodeId: the sample's poses fit them (0 reference at the origin, 1 hips at
103.5 cm, 2-4 left leg down to the foot at 10 cm, 9-11 left arm, 15 head at 165 cm, 23-24 spine 1-2, 50-52 left thumb
next to the left hand, 74-92 right fingers...).
"""
import struct
from typing import List

MAGIC = 0x24022789
NODE_COUNT = 172
RECORD = 120
HEADER = 8 + 4 * NODE_COUNT

_FINGERS = ['Thumb', 'Index', 'Middle', 'Ring', 'Pinky', 'ExtraFinger']
NODE_NAMES = ['Reference', 'Hips', 'LeftUpLeg', 'LeftLeg', 'LeftFoot', 'RightUpLeg', 'RightLeg', 'RightFoot', 'Spine',
              'LeftArm', 'LeftForeArm', 'LeftHand', 'RightArm', 'RightForeArm', 'RightHand', 'Head', 'LeftToeBase',
              'RightToeBase', 'LeftShoulder', 'RightShoulder', 'Neck', 'LeftFingerBase', 'RightFingerBase'] + \
    ['Spine{}'.format(i) for i in range(1, 10)] + ['Neck{}'.format(i) for i in range(1, 10)] + \
    ['LeftUpLegRoll', 'LeftLegRoll', 'RightUpLegRoll', 'RightLegRoll', 'LeftArmRoll', 'LeftForeArmRoll',
     'RightArmRoll', 'RightForeArmRoll', 'HipsTranslation'] + \
    ['{}Hand{}{}'.format(side, f, i) for side in ('Left', 'Right') for f in _FINGERS for i in range(1, 5)]


class EHikcc(Exception):
    pass


def node_name(i: int) -> str:
    return NODE_NAMES[i] if i < len(NODE_NAMES) else 'node {}'.format(i)


class Node:
    __slots__ = ('index', 'translation', 'rotation', 'scale', 'extra')

    def __init__(self, index, translation, rotation, scale, extra):
        self.index, self.translation, self.rotation, self.scale, self.extra = index, translation, rotation, scale, extra

    @property
    def name(self) -> str:
        return node_name(self.index)


def is_hikcc(size: int, head: bytes) -> bool:
    if size < HEADER or len(head) < 8:
        return False
    magic, _version = struct.unpack_from('<II', head, 0)
    return magic == MAGIC and (size - HEADER - 16) % RECORD == 0


class Hikcc:
    def __init__(self, data: bytes):
        if len(data) < HEADER or struct.unpack_from('<I', data, 0)[0] != MAGIC:
            raise EHikcc('not a HumanIK character definition')
        self.version = struct.unpack_from('<I', data, 4)[0]
        flags = struct.unpack_from('<{}I'.format(NODE_COUNT), data, 8)
        if any(f not in (0, 1) for f in flags):
            raise EHikcc('node flags other than 0 / 1')
        used = [i for i, f in enumerate(flags) if f]
        expected = HEADER + RECORD * len(used) + 16
        if len(data) != expected:
            raise EHikcc('{} used nodes: {} bytes expected, {} in the file'.format(len(used), expected, len(data)))
        self.trailer = data[expected - 16:]
        self.nodes: List[Node] = []
        for k, i in enumerate(used):
            v = struct.unpack_from('<30f', data, HEADER + RECORD * k)
            self.nodes.append(Node(i, v[0:3], v[3:7], v[7:10], v[10:30]))


def dump_lines(data: bytes) -> List[str]:
    h = Hikcc(data)
    lines = ['HumanIK character definition (version {}): {} nodes used of {}'.format(
        h.version, len(h.nodes), NODE_COUNT)]
    extra = [n for n in h.nodes if any(abs(x) > 1e-9 for x in n.extra)]
    if extra:
        lines.append('{} nodes have non-zero values in their 20 unknown floats'.format(len(extra)))
    if h.trailer.strip(b'\0'):
        lines.append('trailer: ' + h.trailer.hex(' '))
    lines += ['', 'node                      translation                    rotation (x, y, z, w)            scale']
    for n in h.nodes:
        lines.append('{:3d} {:<20} ({:8.3f}, {:8.3f}, {:8.3f})  ({:6.3f}, {:6.3f}, {:6.3f}, {:6.3f})  ({:.3f}, {:.3f}, {:.3f})'
                     .format(n.index, n.name, *n.translation, *n.rotation, *n.scale))
    return lines
