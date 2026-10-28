"""
Decoding of hkaSplineCompressedAnimation (Havok 2014), the animations of theHunter: COTW (.ban). COTWed DECA,
2026-10-28, from 3 samples (17 animations, 10 253 track blocks) of the game.

ff_havok reads the object (frames, blocks, offsets) but left m_data opaque. Its layout, as found in the samples:

  m_data is cut in blocks (m_blockOffsets, relative to m_data, 16-aligned). A block covers m_maxFramesPerBlock
  frames; consecutive blocks share one frame (block b starts at frame b * (m_maxFramesPerBlock - 1)).
  In a block (offsets and alignments relative to the block start):
    masks : one TransformMask (4 bytes) per transform track, then one byte per float track
            (m_maskAndQuantizationSize bytes in all) :
              u8 quantization : bits 0-1 position, bits 2-5 rotation, bits 6-7 scale
                                (position, scale : 0 = 8 bits, 1 = 16 bits ;
                                 rotation : 0 POLAR32, 1 THREECOMP40, 2 THREECOMP48, 3 THREECOMP24, 4 STRAIGHT16,
                                 5 UNCOMPRESSED)
              u8 position, u8 rotation, u8 scale : flags ; bit i (0x01, 0x02, 0x04, 0x08) = component i static,
                                bit 4 + i (0x10, 0x20, 0x40, 0x80) = component i on a spline ; a rotation is a spline
                                if any of its high bits is set, else static if any low bit is set
    then, per transform track, position, rotation, scale :
      position / scale, spline : u16 n (control points - 1), u8 degree, n + degree + 2 knots (u8, frame numbers),
                                 align 4 ; per component x, y, z : spline -> f32 min, f32 max ; static -> f32 value ;
                                 then n + 1 control points, each spline component quantized (u8 / 255 or
                                 u16 / 65535 between min and max) ; align 4
      position / scale, static : one f32 per static component, in x, y, z order
      rotation, spline         : u16 n, u8 degree, knots ; align to the quantization ; n + 1 quaternions ; align 4
      rotation, static         : align to the quantization ; one quaternion ; align 4
      missing                  : position 0, rotation identity, scale 1
    the transform data ends at m_floatBlockOffsets[b] (relative to the block), where the float tracks begin.
  With float tracks, the masks take 4 * transform tracks + float tracks bytes ; the transform data starts at the next
  multiple of 4 (23-animations: every block of the 2 107 animations with float tracks then ends where expected).
  Float tracks (1 to 82, animals, dogs, birds) start at m_floatBlockOffsets[b] ; their mask byte has the flags of a
  position component : 0x01 static, 0x10 spline, and 0x02 for 16-bit values (8 bits without) [hypothesis from the
  game's masks, 0x03 and 0x12 only : the end of the float data, padded to 16, must be the next block]. Static : f32.
  Spline : u16 n, u8 degree, knots, align 4, f32 min, f32 max, n + 1 quantized values, align 4.
  A value at a frame is the B-spline (de Boor) of its control points at the frame number within the block ; a
  rotation is normalized after evaluation.

Checked on the samples: every block ends exactly at m_floatBlockOffsets ; uncompressed quaternions are unit ; the
last frame of a block equals the first frame of the next one. COTW quantizes nearly every track as 16-bit
position / UNCOMPRESSED rotation / 16-bit scale ; THREECOMP40 and THREECOMP48 are decoded as the usual Havok
packing (3 components of 12 or 15 bits, index of the dropped largest component, sign) ; POLAR32, THREECOMP24,
STRAIGHT16 are not decoded (EHavokAnim), none being seen yet.
"""
import math
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np

ROTATION_QUANTIZATIONS = {0: 'POLAR32', 1: 'THREECOMP40', 2: 'THREECOMP48', 3: 'THREECOMP24', 4: 'STRAIGHT16',
                          5: 'UNCOMPRESSED'}
ROTATION_SIZE = {1: 5, 2: 6, 5: 16}
ROTATION_ALIGN = {0: 4, 1: 1, 2: 2, 3: 1, 4: 2, 5: 4}
VECTOR_QUANTIZATIONS = {0: 'BITS8', 1: 'BITS16'}


class EHavokAnim(Exception):
    pass


def _align(p: int, n: int) -> int:
    return p + (-p) % n


# ---------------------------------------------------------------------------------------------------------
# Quaternions
# ---------------------------------------------------------------------------------------------------------

def _insert_largest(abc, w: float, index: int) -> List[float]:
    """The three stored components fill the quaternion (x, y, z, w) around the dropped one, put back at index."""
    q = list(abc)
    q.insert(index, w)
    return q


def quaternion_threecomp40(b: bytes) -> List[float]:
    v = int.from_bytes(b[:5], 'little')
    frac = 1.0 / (2047.0 * math.sqrt(2.0))
    abc = [(((v >> (12 * i)) & 0xFFF) - 2047) * frac for i in range(3)]
    w = math.sqrt(max(0.0, 1.0 - sum(c * c for c in abc)))
    if (v >> 38) & 1:
        w = -w
    return _insert_largest(abc, w, (v >> 36) & 3)


def quaternion_threecomp48(b: bytes) -> List[float]:
    x, y, z = struct.unpack_from('<3H', b)
    frac = 1.0 / (16383.0 * math.sqrt(2.0))
    index = ((y >> 14) & 2) | ((x >> 15) & 1)
    abc = [((c & 0x7FFF) - 16383) * frac for c in (x, y, z)]
    w = math.sqrt(max(0.0, 1.0 - sum(c * c for c in abc)))
    if z >> 15:
        w = -w
    return _insert_largest(abc, w, index)


def read_quaternion(data: bytes, p: int, quantization: int) -> Tuple[List[float], int]:
    if quantization == 5:
        return list(struct.unpack_from('<4f', data, p)), p + 16
    if quantization == 1:
        return quaternion_threecomp40(data[p:p + 5]), p + 5
    if quantization == 2:
        return quaternion_threecomp48(data[p:p + 6]), p + 6
    raise EHavokAnim('rotation quantization {} ({}) not decoded'.format(
        quantization, ROTATION_QUANTIZATIONS.get(quantization, '?')))


# ---------------------------------------------------------------------------------------------------------
# B-splines
# ---------------------------------------------------------------------------------------------------------

def find_span(n: int, degree: int, u: float, knots) -> int:
    """Knot span of u for n + 1 control points (as Havok: the last span past the end)."""
    if u >= knots[n + 1]:
        return n
    if u <= knots[degree]:
        return degree
    low, high = degree, n + 1
    mid = (low + high) // 2
    while u < knots[mid] or u >= knots[mid + 1]:
        if u < knots[mid]:
            high = mid
        else:
            low = mid
        mid = (low + high) // 2
    return mid


def basis(span: int, u: float, degree: int, knots) -> List[float]:
    """Non-zero B-spline basis functions N[span - degree .. span] at u (Cox-de Boor)."""
    n = [1.0] + [0.0] * degree
    left = [0.0] * (degree + 1)
    right = [0.0] * (degree + 1)
    for j in range(1, degree + 1):
        left[j] = u - knots[span + 1 - j]
        right[j] = knots[span + j] - u
        saved = 0.0
        for r in range(j):
            den = right[r + 1] + left[j - r]
            t = n[r] / den if den != 0 else 0.0
            n[r] = saved + right[r + 1] * t
            saved = left[j - r] * t
        n[j] = saved
    return n


class Curve:
    """A component group (position, rotation or scale) of a track in a block."""
    __slots__ = ('kind', 'value', 'degree', 'knots', 'points', 'quantization')

    def __init__(self, kind: str, value=None, degree: int = 0, knots=None, points=None, quantization=None):
        self.kind = kind                # 'default', 'static' or 'spline'
        self.value = value              # np.array (dim,) for default and static
        self.degree = degree
        self.knots = knots
        self.points = points            # np.array (n + 1, dim)
        self.quantization = quantization

    def evaluate(self, frames: int) -> np.ndarray:
        if self.kind != 'spline':
            return np.tile(self.value, (frames, 1))
        n = len(self.points) - 1
        out = np.zeros((frames, self.points.shape[1]))
        for f in range(frames):
            span = find_span(n, self.degree, float(f), self.knots)
            for j, b in enumerate(basis(span, float(f), self.degree, self.knots)):
                out[f] += b * self.points[span - self.degree + j]
        return out


def dropped_index(b: bytes, quantization: int) -> Optional[int]:
    """Index of the component a THREECOMP40 / THREECOMP48 quaternion does not store (the largest, rebuilt)."""
    if quantization == 1:
        return (int.from_bytes(b[:5], 'little') >> 36) & 3
    if quantization == 2:
        x, y, _z = struct.unpack_from('<3H', b)
        return ((y >> 14) & 2) | ((x >> 15) & 1)
    return None


class BlockReader:
    def __init__(self, data: bytes, start: int, end: int):
        self.data, self.start, self.end = data, start, end
        self.p = start
        # quaternions packed without their largest component : (read, rebuilt component really the largest)
        self.packed = [0, 0]

    def quaternion(self, quantization: int) -> List[float]:
        p = self.p
        q, self.p = read_quaternion(self.data, self.p, quantization)
        i = dropped_index(self.data[p:self.p], quantization)
        if i is not None:
            self.packed[0] += 1
            if abs(q[i]) + 1e-3 >= max(abs(v) for k, v in enumerate(q) if k != i):
                self.packed[1] += 1
        return q

    def align(self, n: int):
        self.p = self.start + _align(self.p - self.start, n)

    def take(self, fmt: str):
        v = struct.unpack_from(fmt, self.data, self.p)
        self.p += struct.calcsize(fmt)
        if self.p > self.end:
            raise EHavokAnim('track data beyond the block ({} > {})'.format(self.p - self.start, self.end - self.start))
        return v

    def knots(self) -> Tuple[int, int, List[int]]:
        n, degree = self.take('<HB')
        knots = list(self.take('<{}B'.format(n + degree + 2)))
        return n, degree, knots

    def vector(self, types: int, quantization: int, default: float) -> Curve:
        value = np.full(3, default, dtype=np.float64)
        spline = [i for i in range(3) if types & (0x10 << i)]
        if spline:
            n, degree, knots = self.knots()
            self.align(4)
            ranges = {}
            for i in range(3):
                if types & (0x10 << i):
                    ranges[i] = self.take('<2f')
                elif types & (1 << i):
                    value[i] = self.take('<f')[0]
            points = np.tile(value, (n + 1, 1))
            fmt, top = ('<B', 255.0) if quantization == 0 else ('<H', 65535.0)
            for c in range(n + 1):
                for i in spline:
                    lo, hi = ranges[i]
                    points[c, i] = lo + (hi - lo) * (self.take(fmt)[0] / top)
            self.align(4)
            return Curve('spline', degree=degree, knots=knots, points=points, quantization=quantization)
        static = [i for i in range(3) if types & (1 << i)]
        for i in static:
            value[i] = self.take('<f')[0]
        return Curve('static' if static else 'default', value=value)

    def rotation(self, types: int, quantization: int) -> Curve:
        if types & 0xF0:
            n, degree, knots = self.knots()
            self.align(ROTATION_ALIGN.get(quantization, 1))
            points = [self.quaternion(quantization) for _c in range(n + 1)]
            if self.p > self.end:
                raise EHavokAnim('rotation data beyond the block')
            self.align(4)
            return Curve('spline', degree=degree, knots=knots, points=np.array(points, dtype=np.float64),
                         quantization=quantization)
        if types & 0x0F:
            self.align(ROTATION_ALIGN.get(quantization, 1))
            q = self.quaternion(quantization)
            self.align(4)
            return Curve('static', value=np.array(q, dtype=np.float64), quantization=quantization)
        return Curve('default', value=np.array([0.0, 0.0, 0.0, 1.0]))


def read_floats(data: bytes, start: int, end: int, p: int, masks: bytes) -> Tuple[List[Curve], int]:
    """Float tracks of a block, from p (absolute) ; returns their curves (dimension 1) and the end (absolute)."""
    r = BlockReader(data, start, end)
    r.p = p
    curves = []
    for m in masks:
        if m & 0xF0:
            n, degree, knots = r.knots()
            r.align(4)
            lo, hi = r.take('<2f')
            fmt, top = ('<H', 65535.0) if m & 0x02 else ('<B', 255.0)
            points = np.array([[lo + (hi - lo) * (r.take(fmt)[0] / top)] for _c in range(n + 1)])
            r.align(4)
            curves.append(Curve('spline', degree=degree, knots=knots, points=points))
        elif m & 0x0F:
            curves.append(Curve('static', value=np.array([r.take('<f')[0]])))
        else:
            curves.append(Curve('default', value=np.zeros(1)))
    return curves, r.p


def read_block(data: bytes, start: int, end: int, n_tracks: int, n_floats: int, masks_size: int,
               transform_end: Optional[int] = None) -> Tuple[List[Tuple[Curve, Curve, Curve]], dict]:
    """Curves (position, rotation, scale) of each transform track of a block, and facts: quantizations seen,
    end of the transform data (relative to the block) and whether it is the expected one, float track masks and
    curves, end of the float data and whether the block ends there (padding to 16), packed quaternions (read,
    coherent)."""
    masks = [data[start + 4 * t:start + 4 * t + 4] for t in range(n_tracks)]
    float_masks = bytes(data[start + 4 * n_tracks:start + 4 * n_tracks + n_floats])
    r = BlockReader(data, start, end)
    r.p = start + _align(masks_size, 4)
    tracks = []
    quant = []
    for qt, pt, rt, st in masks:
        qp, qr, qs = qt & 3, (qt >> 2) & 0xF, (qt >> 6) & 3
        quant.append((qp, qr, qs))
        position = r.vector(pt, qp, 0.0)
        rotation = r.rotation(rt, qr)
        scale = r.vector(st, qs, 1.0)
        tracks.append((position, rotation, scale))
    fin = r.p - start
    facts = {'quantizations': quant, 'end': fin, 'float_masks': float_masks, 'packed': tuple(r.packed),
             'end_ok': transform_end is None or _align(fin, 4) == _align(transform_end, 4),
             'floats': [], 'float_end': None, 'float_end_ok': True}
    if n_floats:
        p = start + (transform_end if transform_end is not None else _align(fin, 4))
        try:
            facts['floats'], f_end = read_floats(data, start, end, p, float_masks)
            facts['float_end'] = f_end - start
            facts['float_end_ok'] = _align(f_end - start, 16) >= end - start - 15 and f_end <= end
        except EHavokAnim:
            facts['floats'] = [Curve('default', value=np.zeros(1)) for _m in float_masks]
            facts['float_end_ok'] = False
    return tracks, facts


def decode(pf, o) -> Dict[str, object]:
    """hkaSplineCompressedAnimation of a ff_havok.Packfile -> dict:
    translations (frames, tracks, 3), rotations (frames, tracks, 4, normalized), scales (frames, tracks, 3),
    track_names (annotation tracks, APEX: the bone of each transform track), frame_duration, duration,
    blocks (per block: frames, end, end_ok), quantizations (Counter of (position, rotation, scale)),
    continuity (largest gap between the last frame of a block and the first of the next: rotation angle in
    radians, translation, scale), quaternion_norm_error (largest | |q| - 1 | of the stored quaternions, before the
    normalization), float_tracks (count), float_masks (Counter of their mask bytes), floats (frames, float
    tracks) (blocks[].float_end_ok: the float data ends where the block does),
    packed_quaternions (THREECOMP40 / 48 read, and how many have their rebuilt component the largest)."""
    import collections
    from . import ff_havok
    if o.class_name != 'hkaSplineCompressedAnimation':
        raise EHavokAnim('{} is not a spline compressed animation'.format(o.class_name))
    info = ff_havok.decode_animation(pf, o)
    s = o.offset
    dt, dn = pf.array(s + 152)
    if dt is None:
        raise EHavokAnim('no m_data')
    base = pf.data_section.start + dt
    data = pf.data[:base + dn]
    n_tracks, n_floats = info['numberOfTransformTracks'], info['numberOfFloatTracks']
    n_frames, per_block = info['numFrames'], info['maxFramesPerBlock']
    offsets, ends = info['blockOffsets'], info['floatBlockOffsets']
    trans = np.zeros((n_frames, n_tracks, 3))
    rots = np.zeros((n_frames, n_tracks, 4))
    scales = np.ones((n_frames, n_tracks, 3))
    floats = np.zeros((n_frames, n_floats))
    blocks = []
    quant = collections.Counter()
    float_masks = collections.Counter()
    packed = [0, 0]
    norm_error = 0.0
    gaps = {'rotation': 0.0, 'translation': 0.0, 'scale': 0.0}
    previous = None
    for b, off in enumerate(offsets):
        first = b * (per_block - 1)
        frames = min(per_block, n_frames - first)
        if frames <= 0:
            break
        end = base + (offsets[b + 1] if b + 1 < len(offsets) else dn)
        tracks, facts = read_block(data, base + off, end, n_tracks, n_floats, info['maskAndQuantizationSize'],
                                   ends[b] if b < len(ends) else None)
        quant.update(facts['quantizations'])
        float_masks.update(facts['float_masks'])
        packed[0] += facts['packed'][0]
        packed[1] += facts['packed'][1]
        for c in tracks:
            raw = c[1].points if c[1].kind == 'spline' else (c[1].value[None, :] if c[1].kind == 'static' else None)
            if raw is not None and len(raw):
                norm_error = max(norm_error, float(np.max(np.abs(np.linalg.norm(raw, axis=1) - 1.0))))
        t = np.stack([c[0].evaluate(frames) for c in tracks], axis=1)
        q = np.stack([c[1].evaluate(frames) for c in tracks], axis=1)
        norm = np.linalg.norm(q, axis=2, keepdims=True)
        q = np.where(norm > 1e-12, q / np.where(norm > 1e-12, norm, 1.0), np.array([0.0, 0.0, 0.0, 1.0]))
        sc = np.stack([c[2].evaluate(frames) for c in tracks], axis=1)
        if previous is not None:
            pt, pq, ps = previous
            dot = np.clip(np.abs(np.sum(pq * q[0], axis=1)), 0.0, 1.0)
            gaps['rotation'] = max(gaps['rotation'], float(np.max(2.0 * np.arccos(dot))))
            gaps['translation'] = max(gaps['translation'], float(np.max(np.abs(pt - t[0]))))
            gaps['scale'] = max(gaps['scale'], float(np.max(np.abs(ps - sc[0]))))
        previous = (t[-1], q[-1], sc[-1])
        trans[first:first + frames] = t
        rots[first:first + frames] = q
        scales[first:first + frames] = sc
        if n_floats:
            floats[first:first + frames] = np.concatenate([c.evaluate(frames) for c in facts['floats']], axis=1)
        blocks.append({'frames': frames, 'end': facts['end'], 'end_ok': facts['end_ok'],
                       'float_end': facts['float_end'], 'float_end_ok': facts['float_end_ok']})
    return {'translations': trans, 'rotations': rots, 'scales': scales,
            'track_names': [a['name'] for a in info['annotationTracks']],
            'frame_duration': info['frameDuration'], 'duration': info['duration'], 'blocks': blocks,
            'quantizations': quant, 'continuity': gaps, 'quaternion_norm_error': norm_error,
            'float_tracks': n_floats, 'float_masks': float_masks, 'floats': floats,
            'packed_quaternions': tuple(packed)}


# ---------------------------------------------------------------------------------------------------------
# Animations of a packfile, tracks -> bones, glTF
# ---------------------------------------------------------------------------------------------------------

def animations_of(pf) -> List[Tuple[object, Optional[dict]]]:
    """(hkaSplineCompressedAnimation object, its hkaAnimationBinding decoded or None), in file order."""
    from . import ff_havok
    bound = {}
    for o in pf.objects:
        if o.class_name == 'hkaAnimationBinding':
            b = ff_havok.decode_binding(pf, o)
            if b['animation'] is not None:
                bound[b['animation'].offset] = b
    return [(o, bound.get(o.offset)) for o in pf.objects if o.class_name == 'hkaSplineCompressedAnimation']


def track_bones(anim: dict, binding: Optional[dict], bone_names: List[str]) -> List[Optional[int]]:
    """Skeleton bone of each transform track. APEX: the binding holds the low 16 bits of hash32(bone name) per track
    (ff_havok.decode_binding); without a binding, the annotation track names."""
    from .ff_havok import bone_hash16
    by_name = {n: i for i, n in enumerate(bone_names)}
    by_hash = {}
    for i, n in enumerate(bone_names):
        by_hash.setdefault(bone_hash16(n), i)
    n_tracks = anim['rotations'].shape[1]
    out: List[Optional[int]] = []
    hashes = (binding or {}).get('transformTrackToBoneIndices') or []
    for t in range(n_tracks):
        bone = by_hash.get(hashes[t]) if t < len(hashes) else None
        if bone is None and t < len(anim['track_names']) and anim['track_names'][t] in by_name:
            bone = by_name[anim['track_names'][t]]
        out.append(bone)
    return out


def gltf_add_animation(gltf, anim: dict, track_nodes: List[Optional[int]], name: str, path_abs: str, uri: str
                       ) -> Optional[int]:
    """Adds a glTF animation (local translation, rotation, scale of the bone nodes, sampled at every frame, linear)
    whose data goes to the binary file path_abs (referenced as uri). Tracks without a node are skipped. Returns the
    animation index, or None when no track has a node."""
    import os
    import pygltflib as pyg
    frames = anim['rotations'].shape[0]
    times = (np.arange(frames, dtype=np.float32) * np.float32(anim['frame_duration'])).astype(np.float32)
    chunks = [times.tobytes()]
    views = [(0, times.nbytes)]
    offset = times.nbytes
    channels = []
    for t, node in enumerate(track_nodes):
        if node is None:
            continue
        for path, values in (('translation', anim['translations'][:, t]), ('rotation', anim['rotations'][:, t]),
                             ('scale', anim['scales'][:, t])):
            data = np.ascontiguousarray(values, dtype=np.float32).tobytes()
            chunks.append(data)
            views.append((offset, len(data)))
            offset += len(data)
            channels.append((node, path, len(views) - 1, values.shape[1]))
    if not channels:
        return None
    os.makedirs(os.path.dirname(path_abs) or '.', exist_ok=True)
    with open(path_abs, 'wb') as f:
        f.write(b''.join(chunks))
    buffer_idx = len(gltf.buffers)
    gltf.buffers.append(pyg.Buffer(uri=uri, byteLength=offset))
    accessors = []
    for i, (off, size) in enumerate(views):
        view_idx = len(gltf.bufferViews)
        gltf.bufferViews.append(pyg.BufferView(buffer=buffer_idx, byteOffset=off, byteLength=size))
        acc = pyg.Accessor(bufferView=view_idx, byteOffset=0, componentType=pyg.FLOAT, count=frames)
        if i == 0:
            acc.type = 'SCALAR'
            acc.min, acc.max = [float(times[0])], [float(times[-1])]
        accessors.append(acc)
    for node, path, view, dim in channels:
        accessors[view].type = 'VEC4' if dim == 4 else 'VEC3'
    first = len(gltf.accessors)
    gltf.accessors.extend(accessors)
    animation = pyg.Animation(name=name)
    for node, path, view, _dim in channels:
        animation.samplers.append(pyg.AnimationSampler(input=first, output=first + view, interpolation='LINEAR'))
        animation.channels.append(pyg.AnimationChannel(sampler=len(animation.samplers) - 1,
                                                       target=pyg.AnimationChannelTarget(node=node, path=path)))
    gltf.animations.append(animation)
    return len(gltf.animations) - 1
