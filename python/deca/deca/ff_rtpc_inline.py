"""
RTPC "en ligne" (inline RTPC) of theHunter: COTW: locations/world.bin and the *.smodc sound modules.

COTWed DECA, 2026-10-09. Deduced from four .smodc files and locations/world.bin, all read to their last byte.
DECA (kk49) had a Kaitai description of world.bin (deca/kaitai/world_bin.py) that read a node as one block of
properties; it fails on nodes that have both children and properties (character_group.smodc).

Layout (little endian, no file header: the file is the root node, which has no name):

    node    := u8 section_count, section_count x section
    section := u16 kind, u16 count, count x item
               kind 4 : item = u32 name_hash, node                      (children)
               kind 5 : item = u32 name_hash, u8 type, value            (properties)

Values, by type (the RTPC variant numbering):

    1 u32           2 f32           3 string: u16 length, bytes (no terminating NUL)
    5 vec3: 3 f32   8 transform: 12 f32 (3x3 rotation, then the translation)
    14 events: u32 count, count x u64

Only these types occur in the samples. Types 4, 6, 7 (vec2, vec4, mat3x3), 9, 10, 11 (u32, f32, u8 arrays with
a u32 count) and 13 (u64 object id) are read with the layout they have in binary RTPC: a hypothesis, flagged as
such in the research log, that the reader turns into an error at the first file that contradicts it.

The section order of every node is kept, so that inline_to_bytes(inline_from_bytes(data)) == data.

Instance lists (2026-10-11, unnamed file 9f542e80 of game34.tab, 5 116 085 bytes, read to the last byte): 159 roots,
each followed by the 9 bytes 01 03 00 04 00 'inst' [a marker; as a node it would be a section of unknown kind 3].
Every root has one child 'weapon_base': the weapon entities (SAttachedEffect, SSoundResourceFMODEvent,
CModelAttachementWeaponComponent...). inline_from_bytes returns them as the children of an InlineInstances root.
This file also confirms the layouts of types 4, 6, 9 and 13 (vec2, vec4, u32 array, object id): 20, 134, 13 479
and 8 627 values, read with the binary RTPC layouts to the last byte.
"""
import struct
from typing import List, Optional, Tuple, Union

KIND_CHILDREN = 4
KIND_PROPERTIES = 5

T_U32, T_F32, T_STR, T_VEC2, T_VEC3, T_VEC4, T_MAT3X3, T_TRANSFORM = 1, 2, 3, 4, 5, 6, 7, 8
T_ARRAY_U32, T_ARRAY_F32, T_ARRAY_U8, T_OBJID, T_EVENT = 9, 10, 11, 13, 14

TYPES_SEEN = frozenset({T_U32, T_F32, T_STR, T_VEC3, T_TRANSFORM, T_EVENT})
TYPE_NAMES = {T_U32: 'u32', T_F32: 'f32', T_STR: 'str', T_VEC2: 'vec2', T_VEC3: 'vec3', T_VEC4: 'vec4',
              T_MAT3X3: 'mat3x3', T_TRANSFORM: 'transform3x4', T_ARRAY_U32: 'u32[]', T_ARRAY_F32: 'f32[]',
              T_ARRAY_U8: 'u8[]', T_OBJID: 'objid', T_EVENT: 'events'}
FIXED_FLOATS = {T_VEC2: 2, T_VEC3: 3, T_VEC4: 4, T_MAT3X3: 9, T_TRANSFORM: 12}

MAX_DEPTH = 64


class EInlineRtpc(Exception):
    pass


class InlineProperty:
    __slots__ = ('name_hash', 'type', 'value', 'pos')

    def __init__(self, name_hash: int, type_: int, value, pos: Optional[int] = None):
        self.name_hash = name_hash
        self.type = type_
        self.value = value
        self.pos = pos

    def __repr__(self):
        return 'InlineProperty(0x{:08x}, {}, {!r})'.format(self.name_hash, TYPE_NAMES.get(self.type, self.type),
                                                           self.value)


INSTANCE_SEPARATOR = b'\x01\x03\x00\x04\x00inst'


class InlineNode:
    """sections: list of (kind, items) in file order; items are InlineNode (kind 4) or InlineProperty (kind 5)."""
    __slots__ = ('name_hash', 'sections', 'pos')

    def __init__(self, name_hash: Optional[int] = None, sections=None, pos: Optional[int] = None):
        self.name_hash = name_hash
        self.sections: List[Tuple[int, list]] = sections if sections is not None else []
        self.pos = pos

    @property
    def children(self) -> List['InlineNode']:
        return [c for kind, items in self.sections if kind == KIND_CHILDREN for c in items]

    @property
    def properties(self) -> List[InlineProperty]:
        return [p for kind, items in self.sections if kind == KIND_PROPERTIES for p in items]

    def walk(self):
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))


class InlineInstances(InlineNode):
    """File of several roots, each followed by INSTANCE_SEPARATOR: the roots are this node's children."""
    __slots__ = ()


# ---------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise EInlineRtpc('truncated at 0x{:x} (need {} bytes)'.format(self.pos, n))
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack('<H', self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack('<I', self.take(4))[0]


def _read_value(r: _Reader, t: int):
    if t == T_U32:
        return r.u32()
    if t == T_F32:
        return struct.unpack('<f', r.take(4))[0]
    if t == T_STR:
        return r.take(r.u16())
    if t in FIXED_FLOATS:
        n = FIXED_FLOATS[t]
        return list(struct.unpack('<{}f'.format(n), r.take(4 * n)))
    if t == T_ARRAY_U32:
        n = r.u32()
        return list(struct.unpack('<{}I'.format(n), r.take(4 * n)))
    if t == T_ARRAY_F32:
        n = r.u32()
        return list(struct.unpack('<{}f'.format(n), r.take(4 * n)))
    if t == T_ARRAY_U8:
        return r.take(r.u32())
    if t == T_OBJID:
        return struct.unpack('<Q', r.take(8))[0]
    if t == T_EVENT:
        n = r.u32()
        return list(struct.unpack('<{}Q'.format(n), r.take(8 * n)))
    raise EInlineRtpc('unknown value type {} at 0x{:x}'.format(t, r.pos - 1))


def _read_node(r: _Reader, name_hash: Optional[int], depth: int) -> InlineNode:
    if depth > MAX_DEPTH:
        raise EInlineRtpc('nodes nested deeper than {} at 0x{:x}'.format(MAX_DEPTH, r.pos))
    node = InlineNode(name_hash, pos=r.pos)
    for _ in range(r.u8()):
        kind, count = r.u16(), r.u16()
        items = []
        if kind == KIND_CHILDREN:
            for _ in range(count):
                h = r.u32()
                items.append(_read_node(r, h, depth + 1))
        elif kind == KIND_PROPERTIES:
            for _ in range(count):
                pos = r.pos
                h = r.u32()
                t = r.u8()
                items.append(InlineProperty(h, t, _read_value(r, t), pos))
        else:
            raise EInlineRtpc('unknown section kind {} at 0x{:x}'.format(kind, r.pos - 4))
        node.sections.append((kind, items))
    return node


def inline_from_bytes(data: bytes) -> InlineNode:
    """Root node of an inline RTPC file; EInlineRtpc if the bytes are not one (including trailing bytes)."""
    r = _Reader(bytes(data))
    root = _read_node(r, None, 0)
    if r.pos != len(r.data) and r.data.startswith(INSTANCE_SEPARATOR, r.pos):
        roots = [root]
        while True:
            if not r.data.startswith(INSTANCE_SEPARATOR, r.pos):
                raise EInlineRtpc('instance {} not followed by the separator (at 0x{:x})'.format(len(roots), r.pos))
            r.pos += len(INSTANCE_SEPARATOR)
            if r.pos == len(r.data):
                return InlineInstances(None, [(KIND_CHILDREN, roots)])
            roots.append(_read_node(r, None, 0))
    if r.pos != len(r.data):
        raise EInlineRtpc('{} bytes after the root node (at 0x{:x})'.format(len(r.data) - r.pos, r.pos))
    return root


def is_inline_rtpc(data: bytes) -> bool:
    """Structural test used by ff_determine: a root with one section of children or properties, and the whole file
    consumed exactly. Inline RTPC has no magic: 01 04 00 is too common to be trusted alone."""
    if len(data) < 5 or data[0] == 0 or data[1:3] not in (b'\x04\x00', b'\x05\x00'):
        return False
    try:
        inline_from_bytes(data)
    except (EInlineRtpc, struct.error, RecursionError):
        return False
    return True


# ---------------------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------------------

def _write_value(out: List[bytes], t: int, v):
    if t == T_U32:
        out.append(struct.pack('<I', v))
    elif t == T_F32:
        out.append(struct.pack('<f', v))
    elif t == T_STR:
        b = bytes(v)
        out.append(struct.pack('<H', len(b)) + b)
    elif t in FIXED_FLOATS:
        n = FIXED_FLOATS[t]
        if len(v) != n:
            raise EInlineRtpc('type {} needs {} floats, got {}'.format(t, n, len(v)))
        out.append(struct.pack('<{}f'.format(n), *v))
    elif t == T_ARRAY_U32:
        out.append(struct.pack('<I{}I'.format(len(v)), len(v), *v))
    elif t == T_ARRAY_F32:
        out.append(struct.pack('<I{}f'.format(len(v)), len(v), *v))
    elif t == T_ARRAY_U8:
        out.append(struct.pack('<I', len(v)) + bytes(v))
    elif t == T_OBJID:
        out.append(struct.pack('<Q', v))
    elif t == T_EVENT:
        out.append(struct.pack('<I{}Q'.format(len(v)), len(v), *v))
    else:
        raise EInlineRtpc('unknown value type {}'.format(t))


def _write_node(out: List[bytes], node: InlineNode):
    out.append(struct.pack('<B', len(node.sections)))
    for kind, items in node.sections:
        out.append(struct.pack('<HH', kind, len(items)))
        for it in items:
            out.append(struct.pack('<I', it.name_hash))
            if kind == KIND_CHILDREN:
                _write_node(out, it)
            else:
                out.append(struct.pack('<B', it.type))
                _write_value(out, it.type, it.value)


def inline_to_bytes(root: InlineNode) -> bytes:
    out: List[bytes] = []
    if isinstance(root, InlineInstances):
        for child in root.children:
            _write_node(out, child)
            out.append(INSTANCE_SEPARATOR)
    else:
        _write_node(out, root)
    return b''.join(out)


# ---------------------------------------------------------------------------------------------------------
# Conversion for the RTPC viewers (deca.ff_rtpc objects)
# ---------------------------------------------------------------------------------------------------------

def _rtpc_value(p: InlineProperty) -> Tuple[int, Union[int, float, bytes, list]]:
    """(binary RTPC type, value as deca.ff_rtpc reads it). The 3x4 transform becomes the 4x4 matrix it stands for
    (rows of the rotation with 0, then the translation with 1), shown as mat4x4."""
    from .ff_rtpc import k_type_mat4x4
    if p.type == T_TRANSFORM:
        m = p.value
        return k_type_mat4x4, [m[0], m[1], m[2], 0.0, m[3], m[4], m[5], 0.0, m[6], m[7], m[8], 0.0,
                               m[9], m[10], m[11], 1.0]
    return p.type, p.value


def to_rtpc(root: InlineNode, version='inline'):
    """deca.ff_rtpc.Rtpc for the RTPC tree viewer (cotw/rtpc_dump.py, deca_gui tree_items.rtpc_tree)."""
    from .ff_rtpc import Rtpc, RtpcNode, RtpcProperty

    def conv(n: InlineNode) -> 'RtpcNode':
        rn = RtpcNode()
        rn.name_hash = n.name_hash or 0
        rn.data_offset = n.pos or 0
        for p in n.properties:
            rp = RtpcProperty()
            rp.name_hash = p.name_hash
            rp.type, rp.data = _rtpc_value(p)
            rp.pos = p.pos or 0
            rp.data_pos = rp.pos + 5
            rp.data_raw = 0
            rn.prop_table.append(rp)
            rn.prop_map[rp.name_hash] = rp
        for c in n.children:
            rc = conv(c)
            rn.child_table.append(rc)
            rn.child_map[rc.name_hash] = rc
        rn.prop_count, rn.child_count = len(rn.prop_table), len(rn.child_table)
        return rn

    r = Rtpc()
    r.magic = b''
    r.version = version
    r.root_node = conv(root)
    return r


def strings_of(root: InlineNode) -> List[bytes]:
    """String values, for name gathering."""
    return [p.value for n in root.walk() for p in n.properties if p.type == T_STR and p.value]
