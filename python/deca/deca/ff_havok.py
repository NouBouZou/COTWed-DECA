"""
Havok packfiles of theHunter: COTW (DECA type h2014): .ban (animations), .bsk (skeletons), .brd (ragdolls),
.navmeshc (AI navigation meshes), .physicsc. 17 579 files, 16.3 GB.

COTWed DECA, 2026-10-09, from 9 samples (3 .ban, 3 .bsk, 2 .navmeshc, 1 .physicsc). Every one is a binary
packfile "hk_2014.1.0-r1", file version 11, layout rules 8 1 0 1 (64-bit pointers, little endian):

  header (0x40 bytes) : magic 57E0E057 10C0C010, user tag, file version, layout rules, section count, contents
                        section and offset (the root object), class-name section and offset, version string,
                        flags, max predicate (u16), predicate array size (u16; bytes after the header)
  section headers     : 0x40 bytes each: 19-byte tag + NUL, absolute data start, then offsets relative to it:
                        local fixups, global fixups, virtual fixups, exports, imports, end
  __classnames__      : records (u32 signature, u8 0x09, class name, NUL)
  __data__            : the objects; the fixup tables at its end say where the pointers are:
      local  fixups  (i32 source, i32 destination)                 pointer to data of the same section
      global fixups  (i32 source, i32 section, i32 destination)    pointer to another object
      virtual fixups (i32 source, i32 section, i32 class name)     an object starts at source, of that class
    (-1 pads each table)

Members of the classes decoded here were checked against the pointer positions of the samples (a pointer member
always has a fixup when its array is not empty). Their order is that of the Havok 2014 SDK; the community
library HavokLib (GPL, not used as code) documents the same order.

APEX particularity (2026-10-09): hkaAnimationBinding.transformTrackToBoneIndices does not hold bone indices but
the low 16 bits of hash32 (Jenkins, as DECA's hash32_func) of each track's bone name; the names are those of the
animation's annotation tracks, one per transform track (RightShoulder -> 0xee80, RightArm -> 0xb6cf).
"""
import struct
from typing import Dict, List, Optional, Tuple

MAGIC = b'\x57\xE0\xE0\x57\x10\xC0\xC0\x10'

# hkaAnimation::AnimationType of Havok 2014 (delta and wavelet compression were removed before 2014): every
# hkaSplineCompressedAnimation of the samples has type 3
ANIMATION_TYPES = {0: 'unknown', 1: 'interleaved', 2: 'mirrored', 3: 'spline compressed', 4: 'quantized compressed',
                   5: 'predictive compressed', 6: 'reference pose'}
ANIMATION_CLASSES = {'hkaSplineCompressedAnimation', 'hkaInterleavedUncompressedAnimation',
                     'hkaQuantizedAnimation', 'hkaPredictiveCompressedAnimation', 'hkaDeltaCompressedAnimation',
                     'hkaWaveletCompressedAnimation', 'hkaMirroredAnimation', 'hkaReferencePoseAnimation'}


class EHavok(Exception):
    pass


class Section:
    __slots__ = ('index', 'name', 'start', 'local_fixups', 'global_fixups', 'virtual_fixups', 'exports', 'imports',
                 'end', 'local', 'glob', 'virtual')

    def __init__(self, index, name, start, offsets):
        self.index, self.name, self.start = index, name, start
        (self.local_fixups, self.global_fixups, self.virtual_fixups, self.exports, self.imports,
         self.end) = offsets
        self.local: Dict[int, int] = {}
        self.glob: Dict[int, Tuple[int, int]] = {}
        self.virtual: List[Tuple[int, int, int]] = []


class HkObject:
    __slots__ = ('offset', 'class_name', 'size')

    def __init__(self, offset: int, class_name: str, size: int):
        self.offset, self.class_name, self.size = offset, class_name, size

    def __repr__(self):
        return 'HkObject(0x{:x}, {}, {} bytes)'.format(self.offset, self.class_name, self.size)


class Packfile:
    def __init__(self, data: bytes):
        self.data = bytes(data)
        b = self.data
        if len(b) < 0x40 or b[:8] != MAGIC:
            raise EHavok('not a Havok packfile (magic {})'.format(b[:8].hex()))
        self.user_tag, self.file_version = struct.unpack_from('<ii', b, 8)
        self.layout = tuple(b[16:20])
        (self.section_count, self.contents_section, self.contents_offset, self.classnames_section,
         self.classnames_offset) = struct.unpack_from('<5i', b, 20)
        self.version = b[40:56].split(b'\0')[0].decode('latin-1')
        self.flags, self.max_predicate, self.predicate_size = struct.unpack_from('<iHH', b, 56)
        if self.layout[0] != 8 or self.layout[1] != 1:
            raise EHavok('layout rules {} not handled (64-bit little endian only)'.format(self.layout))
        if not 1 <= self.section_count <= 16:
            raise EHavok('{} sections'.format(self.section_count))
        base = 0x40 + (self.predicate_size if self.max_predicate != 0xFFFF else 0)
        self.sections: List[Section] = []
        for i in range(self.section_count):
            so = base + 0x40 * i
            name = b[so:so + 19].split(b'\0')[0].decode('latin-1')
            start = struct.unpack_from('<i', b, so + 20)[0]
            offsets = struct.unpack_from('<6i', b, so + 24)
            if start < 0 or start + offsets[-1] > len(b):
                raise EHavok('section {} ends beyond the file'.format(name))
            self.sections.append(Section(i, name, start, offsets))
        for s in self.sections:
            self._read_fixups(s)
        self.class_names = self._read_class_names()
        self.objects = self._objects()
        self._by_offset = {o.offset: o for o in self.objects}

    # -- tables -------------------------------------------------------------------------------------------

    def _read_fixups(self, s: Section):
        b, st = self.data, s.start
        p = st + s.local_fixups
        while p + 8 <= st + s.global_fixups:
            src, dst = struct.unpack_from('<ii', b, p)
            p += 8
            if src != -1:
                s.local[src] = dst
        p = st + s.global_fixups
        while p + 12 <= st + s.virtual_fixups:
            src, sec, dst = struct.unpack_from('<iii', b, p)
            p += 12
            if src != -1:
                s.glob[src] = (sec, dst)
        p = st + s.virtual_fixups
        while p + 12 <= st + s.exports:
            src, sec, name = struct.unpack_from('<iii', b, p)
            p += 12
            if src != -1:
                s.virtual.append((src, sec, name))

    def _read_class_names(self) -> Dict[int, str]:
        s = self.section('__classnames__') or self.sections[self.classnames_section]
        b, st, end = self.data, s.start, s.start + s.local_fixups
        out, p = {}, st
        while p + 5 < end and b[p] != 0xFF:
            if b[p + 4] != 0x09:
                raise EHavok('class name record without 0x09 at 0x{:x}'.format(p))
            q = b.index(b'\0', p + 5)
            out[p + 5 - st] = b[p + 5:q].decode('latin-1')
            p = q + 1
        return out

    def _objects(self) -> List[HkObject]:
        d = self.data_section
        objs = sorted((src, self.class_names.get(name, '?class@0x{:x}'.format(name))) for src, _sec, name in d.virtual)
        ends = [o for o, _n in objs[1:]] + [d.local_fixups]
        return [HkObject(o, n, e - o) for (o, n), e in zip(objs, ends)]

    def section(self, name: str) -> Optional[Section]:
        for s in self.sections:
            if s.name == name:
                return s
        return None

    @property
    def data_section(self) -> Section:
        return self.section('__data__') or self.sections[self.contents_section]

    # -- reading the data section (offsets relative to its start) -------------------------------------------

    def _abs(self, off: int) -> int:
        return self.data_section.start + off

    def u8(self, off): return self.data[self._abs(off)]
    def i16(self, off): return struct.unpack_from('<h', self.data, self._abs(off))[0]
    def u16(self, off): return struct.unpack_from('<H', self.data, self._abs(off))[0]
    def i32(self, off): return struct.unpack_from('<i', self.data, self._abs(off))[0]
    def u32(self, off): return struct.unpack_from('<I', self.data, self._abs(off))[0]
    def f32(self, off): return struct.unpack_from('<f', self.data, self._abs(off))[0]

    def floats(self, off, n) -> List[float]:
        return list(struct.unpack_from('<{}f'.format(n), self.data, self._abs(off)))

    def ptr(self, off) -> Optional[int]:
        """Target (data offset) of the pointer at off, or None (null pointer: no fixup)."""
        d = self.data_section
        if off in d.local:
            return d.local[off]
        if off in d.glob:
            sec, dst = d.glob[off]
            return dst if sec == d.index else None
        return None

    def cstr(self, off) -> Optional[str]:
        t = self.ptr(off)
        if t is None:
            return None
        a = self._abs(t)
        return self.data[a:self.data.index(b'\0', a)].decode('utf-8', 'replace')

    def array(self, off) -> Tuple[Optional[int], int]:
        """hkArray at off: (target, count). 16 bytes: pointer, i32 size, i32 capacity and flags."""
        return self.ptr(off), self.i32(off + 8)

    def object_at(self, off: Optional[int]) -> Optional[HkObject]:
        return None if off is None else self._by_offset.get(off)

    def root(self) -> Optional[HkObject]:
        return self.object_at(self.contents_offset)


# ---------------------------------------------------------------------------------------------------------
# Class decoders: field name -> value; ('obj', HkObject) for references, lists for arrays
# ---------------------------------------------------------------------------------------------------------

def _refs(pf: Packfile, off: int) -> List[Optional[HkObject]]:
    target, n = pf.array(off)
    return [pf.object_at(pf.ptr(target + 8 * i)) for i in range(n)] if target is not None else []


def decode_root(pf: Packfile, o: HkObject) -> dict:
    target, n = pf.array(o.offset)
    variants = []
    for i in range(n if target is not None else 0):
        e = target + 24 * i
        variants.append({'name': pf.cstr(e), 'className': pf.cstr(e + 8), 'variant': pf.object_at(pf.ptr(e + 16))})
    return {'namedVariants': variants}


def decode_animation_container(pf: Packfile, o: HkObject) -> dict:
    return {name: _refs(pf, o.offset + off) for name, off in
            (('skeletons', 16), ('animations', 32), ('bindings', 48), ('attachments', 64), ('skins', 80))}


def decode_skeleton(pf: Packfile, o: HkObject) -> dict:
    s = o.offset
    out = {'name': pf.cstr(s + 16)}
    pt, pn = pf.array(s + 24)
    parents = [pf.i16(pt + 2 * i) for i in range(pn)] if pt is not None else []
    bt, bn = pf.array(s + 40)
    rt, rn = pf.array(s + 56)
    bones = []
    for i in range(bn if bt is not None else 0):
        bone = {'name': pf.cstr(bt + 16 * i), 'lockTranslation': bool(pf.u8(bt + 16 * i + 8)),
                'parent': parents[i] if i < len(parents) else None}
        if rt is not None and i < rn:
            q = pf.floats(rt + 48 * i, 12)
            bone['translation'], bone['rotation'], bone['scale'] = q[0:3], q[4:8], q[8:11]
        bones.append(bone)
    out['bones'] = bones
    ft, fn = pf.array(s + 72)
    out['referenceFloats'] = pf.floats(ft, fn) if ft is not None else []
    st, sn = pf.array(s + 88)
    out['floatSlots'] = [pf.cstr(st + 8 * i) for i in range(sn)] if st is not None else []
    out['localFrames'] = pf.array(s + 104)[1]
    at, an = pf.array(s + 120)
    out['partitions'] = [{'name': pf.cstr(at + 16 * i), 'startBoneIndex': pf.i16(at + 16 * i + 8),
                          'numBones': pf.u16(at + 16 * i + 10)} for i in range(an)] if at is not None else []
    return out


def decode_animation(pf: Packfile, o: HkObject) -> dict:
    s = o.offset
    t = pf.u32(s + 16)
    out = {'type': '{} ({})'.format(t, ANIMATION_TYPES.get(t, '?')), 'duration': pf.f32(s + 20),
           'numberOfTransformTracks': pf.i32(s + 24), 'numberOfFloatTracks': pf.i32(s + 28),
           'extractedMotion': pf.object_at(pf.ptr(s + 32))}
    tt, tn = pf.array(s + 40)
    tracks = []
    for i in range(tn if tt is not None else 0):
        e = tt + 24 * i
        at, an = pf.array(e + 8)
        tracks.append({'name': pf.cstr(e), 'annotations': [
            {'time': pf.f32(at + 16 * j), 'text': pf.cstr(at + 16 * j + 8)} for j in range(an)] if at is not None else []})
    out['annotationTracks'] = tracks
    if o.class_name == 'hkaSplineCompressedAnimation':
        out.update({'numFrames': pf.i32(s + 56), 'numBlocks': pf.i32(s + 60), 'maxFramesPerBlock': pf.i32(s + 64),
                    'maskAndQuantizationSize': pf.i32(s + 68), 'blockDuration': pf.f32(s + 72),
                    'blockInverseDuration': pf.f32(s + 76), 'frameDuration': pf.f32(s + 80)})
        for name, off in (('blockOffsets', 88), ('floatBlockOffsets', 104), ('transformOffsets', 120),
                          ('floatOffsets', 136)):
            at, an = pf.array(s + off)
            out[name] = [pf.u32(at + 4 * i) for i in range(an)] if at is not None else []
        out['data'] = '{} bytes (compressed spline blocks, not decoded)'.format(pf.array(s + 152)[1])
    return out


def bone_hash16(name: str) -> int:
    from .hashes import hash32_func
    return int(hash32_func(name.encode('utf-8'))) & 0xFFFF


def decode_binding(pf: Packfile, o: HkObject) -> dict:
    s = o.offset
    out = {'originalSkeletonName': pf.cstr(s + 16), 'animation': pf.object_at(pf.ptr(s + 24))}
    for name, off in (('transformTrackToBoneIndices', 32), ('floatTrackToFloatSlotIndices', 48),
                      ('partitionIndices', 64)):
        at, an = pf.array(s + off)
        out[name] = [pf.u16(at + 2 * i) for i in range(an)] if at is not None else []
    # APEX: low 16 bits of hash32(bone name), resolved with the annotation track names of the animation
    anim = out['animation']
    names = {}
    if anim is not None and anim.class_name in ANIMATION_CLASSES:
        for t in decode_animation(pf, anim)['annotationTracks']:
            if t['name']:
                names.setdefault(bone_hash16(t['name']), t['name'])
    out['trackBones'] = [names.get(v) for v in out['transformTrackToBoneIndices']]
    out['blendHint'] = {0: 'normal', 1: 'additive (deprecated)', 2: 'additive'}.get(pf.u8(s + 80), pf.u8(s + 80))
    return out


NAVMESH_ARRAYS = (('faces', 16), ('edges', 32), ('vertices', 48), ('streamingSets', 64), ('faceData', 80),
                  ('edgeData', 96))


def decode_navmesh(pf: Packfile, o: HkObject) -> dict:
    """hkaiNavMesh: the six arrays (member order of the SDK, pointers checked on 2 samples) and the vertices."""
    s = o.offset
    out = {name: '{} elements'.format(pf.array(s + off)[1]) for name, off in NAVMESH_ARRAYS}
    vt, vn = pf.array(s + 48)
    if vt is not None and vn:
        xs = [pf.floats(vt + 16 * i, 3) for i in range(vn)]
        lo = [min(v[k] for v in xs) for k in range(3)]
        hi = [max(v[k] for v in xs) for k in range(3)]
        out['vertices'] = '{} elements, bounding box {} to {}'.format(
            vn, ' '.join('{:.1f}'.format(x) for x in lo), ' '.join('{:.1f}'.format(x) for x in hi))
    out['faceDataStriding'], out['edgeDataStriding'] = pf.i32(s + 112), pf.i32(s + 116)
    return out


def decode_generic(pf: Packfile, o: HkObject) -> dict:
    """Any class: the pointer members of the object's fixed part, read from the fixups. A pointer followed by a
    plausible element count is shown as an array; a pointer to text as a string; a global one as an object."""
    d = pf.data_section
    start, end = o.offset, o.offset + o.size
    out = {}
    targets = sorted(set(d.local.values()))
    for src in sorted(k for k in list(d.local) + list(d.glob) if start <= k < end):
        rel = src - start
        if src in d.glob:
            out['+0x{:x}'.format(rel)] = pf.object_at(pf.ptr(src))
            continue
        t = d.local[src]
        if t < end and t >= start and rel >= _fixed_size(pf, o):
            continue        # pointer inside the object's own arrays: shown through its array
        n = pf.i32(src + 8) if src + 16 <= end else 0
        cap = pf.u32(src + 12) if src + 16 <= end else 0
        text = _text_at(pf, t)
        if text is not None and not (0 < n <= cap & 0x3FFFFFFF):
            out['+0x{:x}'.format(rel)] = text
        elif 0 < n <= (cap & 0x3FFFFFFF):
            nxt = next((x for x in targets if x > t), end)
            out['+0x{:x}'.format(rel)] = 'array: {} elements (~{} bytes each)'.format(n, max(0, (nxt - t)) // n)
        else:
            out['+0x{:x}'.format(rel)] = 'pointer to +0x{:x}'.format(t - start)
    return out


def _fixed_size(pf: Packfile, o: HkObject) -> int:
    """End of the object's fixed part: the first target of its own pointers (its arrays follow it)."""
    d = pf.data_section
    own = [d.local[k] for k in d.local if o.offset <= k < o.offset + o.size and o.offset <= d.local[k] < o.offset + o.size]
    return (min(own) - o.offset) if own else o.size


def _text_at(pf: Packfile, t: int) -> Optional[str]:
    a = pf._abs(t)
    end = pf.data.find(b'\0', a, a + 256)
    if end <= a:
        return None
    raw = pf.data[a:end]
    if all(32 <= c < 127 for c in raw):
        return raw.decode('ascii')
    return None


DECODERS = {'hkRootLevelContainer': decode_root, 'hkaAnimationContainer': decode_animation_container,
            'hkaSkeleton': decode_skeleton, 'hkaAnimationBinding': decode_binding, 'hkaiNavMesh': decode_navmesh}


def decode(pf: Packfile, o: HkObject) -> dict:
    if o.class_name in DECODERS:
        return DECODERS[o.class_name](pf, o)
    if o.class_name in ANIMATION_CLASSES:
        return decode_animation(pf, o)
    return decode_generic(pf, o)


def is_packfile(data: bytes) -> bool:
    return data[:8] == MAGIC


def summary_lines(pf: Packfile) -> List[str]:
    lines = ['Havok packfile {} (file version {}), {} sections, {} objects'.format(
        pf.version, pf.file_version, pf.section_count, len(pf.objects))]
    counts = {}
    for o in pf.objects:
        counts[o.class_name] = counts.get(o.class_name, 0) + 1
    lines += ['  {:>5} x {}'.format(n, c) for c, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return lines
