"""
Collision shapes of theHunter: COTW: Havok Physics 2014 ("hknp") in the PFX files (*.pfxc: model physics, *.pfs:
location physics; ff_pfx.py) and the *.physicsc packfiles (vehicles).

COTWed DECA, 2026-10-29. Layouts read on 12 PFX samples and 3 .physicsc of the game (offsets inside the object;
Havok 2014.1, 64-bit; checked by the polytope invariants below on every shape of the samples):

hknpShape (every shape)
    +0x10 u16 flags, u8 numShapeKeyBits, u8 dispatchType ; +0x14 f32 convexRadius ; +0x18 u64 userData ;
    +0x20 hkRefCountedProperties*
hknpConvexShape (hknpConvexShape, hknpSphereShape) and its subclass hknpConvexPolytopeShape (also hknpCapsuleShape)
    +0x30 vertices: hkRelArray<hkcdVertex>: u16 count, u16 offset from the field ; a vertex is x, y, z and, in w, the
          vertex index (bits 0x3f0000nn); the array is padded to a multiple of 4 with copies of the last vertex
    polytope:
    +0x40 planes: hkRelArray<hkVector4> (normal, distance: n.v + d = 0 on the face, < 0 inside), padded like the
          vertices (the faces count is the number of real planes)
    +0x44 faces: hkRelArray of 4 bytes: u16 first index, u8 number of indices, u8 minimal half angle
    +0x48 indices: hkRelArray<u8> (vertex numbers, a face after the other)
    capsule (a polytope that is a thin box around the segment): +0x50 a, +0x60 b (hkVector4); radius = convexRadius
    sphere (a convex shape of 4 copies of the center): radius = convexRadius
hknpStaticCompoundShape, hknpDynamicCompoundShape
    +0x60 instances: hkArray (free list) of 0x80-byte hknpShapeInstance: +0x00 rotation columns 0, 1, 2 and +0x30
          translation (hkTransform; the w components carry packed flags, ignored), +0x40 scale, +0x50 hknpShape*
hknpPhysicsSystemData
    +0x40 bodyCinfos: hkArray of 0x60-byte hknpBodyCinfo: +0x00 hknpShape*, +0x20 name, +0x30 position,
          +0x40 orientation (quaternion x, y, z, w)
hkGeometry (.physicsc): +0x10 vertices hkArray<hkVector4>, +0x20 triangles hkArray of 16 bytes (i32 a, b, c,
    material). hknpExternMeshShape (which uses them at run time) is not decoded: its geometry pointer is not in the file.

What is not read: materials, motion properties, constraints, mass properties, the shape trees (hkcdStaticTree) of the
compound and extern mesh shapes.
"""
import math
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import ff_havok as H

POLYTOPE_CLASSES = {'hknpConvexPolytopeShape', 'hknpCapsuleShape'}
CONVEX_CLASSES = {'hknpConvexShape', 'hknpSphereShape'} | POLYTOPE_CLASSES
COMPOUND_CLASSES = {'hknpStaticCompoundShape', 'hknpDynamicCompoundShape'}
SHAPE_CLASSES = CONVEX_CLASSES | COMPOUND_CLASSES | {'hknpExternMeshShape', 'hknpHeightFieldShape',
                                                      'hknpCompressedHeightFieldShape', 'hknpScaledConvexShape',
                                                      'hknpMaskedCompoundShape', 'hknpTriangleShape'}
INSTANCE_SIZE = 0x80
BODY_SIZE = 0x60


class EHavokPhysics(Exception):
    pass


class Shape:
    """A decoded collision shape (local space). kind: 'polytope', 'capsule', 'sphere', 'convex', 'compound' or
    'unknown' (class not decoded)."""

    def __init__(self, class_name: str, offset: int):
        self.class_name, self.offset = class_name, offset
        self.kind = 'unknown'
        self.convex_radius = 0.0
        self.vertices = np.zeros((0, 3))
        self.planes = np.zeros((0, 4))
        self.faces: List[Tuple[int, int]] = []          # (first index, number of indices)
        self.indices = np.zeros(0, dtype=np.int64)
        self.a = self.b = None
        self.instances: List[Tuple[np.ndarray, np.ndarray, Optional['Shape']]] = []   # (4x4, scale, shape)

    def face_vertices(self, i: int) -> np.ndarray:
        first, n = self.faces[i]
        return self.indices[first:first + n]

    def __repr__(self):
        return 'Shape({}, {} vertices, {} faces, {} instances)'.format(self.class_name, len(self.vertices),
                                                                     len(self.faces), len(self.instances))


class Body:
    def __init__(self, name: Optional[str], position, orientation, shape: Optional[Shape]):
        self.name, self.position, self.orientation, self.shape = name, position, orientation, shape

    def matrix(self) -> np.ndarray:
        return rigid_matrix(self.orientation, self.position)


def rigid_matrix(q, t) -> np.ndarray:
    """4x4 matrix of the rotation q (x, y, z, w) followed by the translation t."""
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    m = np.identity(4)
    m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                 [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                 [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    m[:3, 3] = [float(v) for v in t[:3]]
    return m


def rel_array(pf: H.Packfile, field: int) -> Tuple[Optional[int], int]:
    """hkRelArray at field: (target, count); the offset is relative to the field itself."""
    count, rel = pf.u16(field), pf.u16(field + 2)
    return (field + rel if count else None), count


def _vectors(pf: H.Packfile, target: Optional[int], n: int, k: int = 4) -> np.ndarray:
    if target is None or n == 0:
        return np.zeros((0, k))
    a = pf._abs(target)
    return np.frombuffer(pf.data, dtype='<f4', count=4 * n, offset=a).reshape(n, 4)[:, :k].astype(np.float64)


class Reader:
    """Decodes the shapes and bodies of a packfile; each shape once (shared by offset)."""

    def __init__(self, pf: H.Packfile):
        self.pf = pf
        self.shapes: Dict[int, Shape] = {}

    def shape(self, off: Optional[int], depth: int = 0) -> Optional[Shape]:
        if off is None:
            return None
        if off in self.shapes:
            return self.shapes[off]
        o = self.pf.object_at(off)
        if o is None:
            raise EHavokPhysics('no object at 0x{:x}'.format(off))
        s = Shape(o.class_name, off)
        self.shapes[off] = s
        if depth > 16:
            raise EHavokPhysics('compound shapes nested more than 16 deep at 0x{:x}'.format(off))
        pf = self.pf
        if o.class_name in CONVEX_CLASSES:
            s.convex_radius = pf.f32(off + 0x14)
            vt, vn = rel_array(pf, off + 0x30)
            s.vertices = _vectors(pf, vt, vn, 3)
            s.kind = 'convex'
            if o.class_name in POLYTOPE_CLASSES:
                pt, pn = rel_array(pf, off + 0x40)
                ft, fn = rel_array(pf, off + 0x44)
                it, inn = rel_array(pf, off + 0x48)
                s.faces = [(pf.u16(ft + 4 * i), pf.u8(ft + 4 * i + 2)) for i in range(fn)]
                s.planes = _vectors(pf, pt, min(pn, fn))
                s.indices = (np.frombuffer(pf.data, dtype=np.uint8, count=inn, offset=pf._abs(it)).astype(np.int64)
                             if inn else np.zeros(0, dtype=np.int64))
                s.kind = 'polytope'
            if o.class_name == 'hknpCapsuleShape':
                s.a, s.b = np.array(pf.floats(off + 0x50, 3)), np.array(pf.floats(off + 0x60, 3))
                s.kind = 'capsule'
            elif o.class_name == 'hknpSphereShape':
                s.kind = 'sphere'
        elif o.class_name in COMPOUND_CLASSES:
            s.kind = 'compound'
            t, n = pf.array(off + 0x60)
            for i in range(n if t is not None else 0):
                e = t + INSTANCE_SIZE * i
                child = pf.ptr(e + 0x50)
                if child is None:
                    continue                              # free slot of the free list
                cols = [pf.floats(e + 16 * k, 3) for k in range(4)]
                m = np.identity(4)
                m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = cols
                s.instances.append((m, np.array(pf.floats(e + 0x40, 3)), self.shape(child, depth + 1)))
        return s

    def bodies(self) -> List[Body]:
        out = []
        pf = self.pf
        for o in pf.objects:
            if o.class_name != 'hknpPhysicsSystemData':
                continue
            t, n = pf.array(o.offset + 0x40)
            for i in range(n if t is not None else 0):
                e = t + BODY_SIZE * i
                out.append(Body(pf.cstr(e + 0x20), pf.floats(e + 0x30, 3), pf.floats(e + 0x40, 4),
                                self.shape(pf.ptr(e))))
        return out

    def geometries(self) -> List[Tuple[str, np.ndarray, np.ndarray]]:
        """hkGeometry objects (.physicsc): (label, vertices (n, 3), triangles (m, 3))."""
        out = []
        pf = self.pf
        for o in pf.objects:
            if o.class_name != 'hkGeometry':
                continue
            vt, vn = pf.array(o.offset + 0x10)
            tt, tn = pf.array(o.offset + 0x20)
            v = _vectors(pf, vt, vn, 3)
            t = (np.frombuffer(pf.data, dtype='<i4', count=4 * tn, offset=pf._abs(tt)).reshape(tn, 4)[:, :3]
                 .astype(np.int64) if tt is not None and tn else np.zeros((0, 3), dtype=np.int64))
            out.append(('hkGeometry@0x{:x}'.format(o.offset), v, t))
        return out


# ---------------------------------------------------------------------------------------------------------
# Checks (the proof that the layout is right: a wrong offset breaks them at once)
# ---------------------------------------------------------------------------------------------------------

def polytope_errors(s: Shape) -> Dict[str, float]:
    """For a polytope: largest |n.v + d| of a face's vertices on its plane, largest n.v + d of any vertex over any
    plane (convexity), largest | |n| - 1 |, and whether the faces and indices stay in range (1.0 = no)."""
    nv = len(s.vertices)
    bad = 0.0
    on_plane = 0.0
    for i, (first, n) in enumerate(s.faces):
        if first + n > len(s.indices) or n < 3:
            bad = 1.0
            continue
        idx = s.face_vertices(i)
        if idx.max(initial=0) >= nv or i >= len(s.planes):
            bad = 1.0
            continue
        p = s.planes[i]
        on_plane = max(on_plane, float(np.abs(s.vertices[idx] @ p[:3] + p[3]).max()))
    if len(s.planes) and nv:
        outside = float((s.vertices @ s.planes[:, :3].T + s.planes[:, 3]).max())
        norm = float(np.abs(np.linalg.norm(s.planes[:, :3], axis=1) - 1).max())
    else:
        outside = norm = 0.0
    return {'on_plane': on_plane, 'outside': outside, 'normal': norm, 'range': bad}


def polytope_closed(s: Shape) -> bool:
    """True when the face contours close the polytope: every edge (i, j) of a face is walked back (j, i) by exactly
    one other face. It holds when the indices of a face follow its contour (the fan triangulation of shape_mesh relies
    on it); flat or degenerate polytopes of the game break it (11 of 3 036 in the samples)."""
    edges: Dict[Tuple[int, int], int] = {}
    for i in range(len(s.faces)):
        idx = [int(x) for x in s.face_vertices(i)]
        for k in range(len(idx)):
            e = (idx[k], idx[(k + 1) % len(idx)])
            edges[e] = edges.get(e, 0) + 1
    return bool(edges) and all(n == 1 and edges.get((b, a), 0) == 1 for (a, b), n in edges.items())


def leaf_points(s: Optional[Shape], matrix: np.ndarray) -> np.ndarray:
    """Extreme points of the convex leaves of a shape tree, placed by matrix: polytope and convex vertices, capsule
    and sphere centres +- their radius on each axis."""
    if s is None:
        return np.zeros((0, 3))
    if s.kind == 'compound':
        parts = [leaf_points(c, matrix @ m @ np.diag([sc[0], sc[1], sc[2], 1.0])) for m, sc, c in s.instances]
        parts = [p for p in parts if len(p)]
        return np.vstack(parts) if parts else np.zeros((0, 3))
    if s.kind in ('sphere', 'capsule'):
        centres = [s.vertices[0]] if s.kind == 'sphere' else [s.a, s.b]
        if s.kind == 'sphere' and not len(s.vertices):
            return np.zeros((0, 3))
        d = np.vstack([np.identity(3), -np.identity(3)]) * s.convex_radius
        v = np.vstack([c + d for c in centres])
    else:
        v = s.vertices
    if not len(v):
        return np.zeros((0, 3))
    return (np.c_[v, np.ones(len(v))] @ matrix.T)[:, :3]


def compound_aabb_excess(pf: H.Packfile, s: Shape) -> Optional[float]:
    """How far the leaves of a compound shape reach outside the bounding box stored in it (+0x80 min, +0x90 max):
    0 when the instance transforms are read right (Havok may store a looser box, never a smaller one)."""
    p = leaf_points(s, np.identity(4))
    if not len(p):
        return None
    lo, hi = np.array(pf.floats(s.offset + 0x80, 3)), np.array(pf.floats(s.offset + 0x90, 3))
    return float(max(0.0, (lo - p.min(0)).max(), (p.max(0) - hi).max()))


# ---------------------------------------------------------------------------------------------------------
# Triangle meshes (for export)
# ---------------------------------------------------------------------------------------------------------

def _rings(top, rings: List[np.ndarray], bottom) -> Tuple[np.ndarray, np.ndarray]:
    """Closed mesh from a top pole, rings of m points (same count, same orientation) and a bottom pole."""
    m = len(rings[0])
    v = [np.asarray(top, dtype=np.float64)] + list(rings) + [np.asarray(bottom, dtype=np.float64)]
    v = np.vstack([x.reshape(-1, 3) for x in v])
    t = [[0, 1 + (j + 1) % m, 1 + j] for j in range(m)]
    for i in range(len(rings) - 1):
        for j in range(m):
            a, b = 1 + i * m + j, 1 + i * m + (j + 1) % m
            t += [[a, b, b + m], [a, b + m, a + m]]
    last = len(v) - 1
    base = 1 + (len(rings) - 1) * m
    t += [[last, base + j, base + (j + 1) % m] for j in range(m)]
    return v, np.array(t, dtype=np.int64)


def _ring(theta: float, r: float, m: int) -> np.ndarray:
    """Points of latitude theta (0 at the top pole) on a sphere of radius r centred on the origin, axis y."""
    ph = 2 * np.pi * np.arange(m) / m
    return np.c_[r * math.sin(theta) * np.cos(ph), np.full(m, r * math.cos(theta)), r * math.sin(theta) * np.sin(ph)]


def _sphere(center, r, n=8) -> Tuple[np.ndarray, np.ndarray]:
    """UV sphere: n - 1 rings of 2n points."""
    c = np.asarray(center, dtype=np.float64)
    rings = [_ring(math.pi * i / n, r, 2 * n) + c for i in range(1, n)]
    return _rings(c + [0, r, 0], rings, c - [0, r, 0])


def _capsule(a, b, r, n=8) -> Tuple[np.ndarray, np.ndarray]:
    """Capsule a-b of radius r: hemisphere around b, the equator repeated at b and at a, hemisphere around a."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    axis = b - a
    length = float(np.linalg.norm(axis))
    y = axis / length if length > 1e-12 else np.array([0.0, 1.0, 0.0])
    x = np.cross(y, [1.0, 0.0, 0.0] if abs(y[0]) < 0.9 else [0.0, 0.0, 1.0])
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    rot = np.stack([x, y, z], axis=1)                       # local y -> capsule axis
    h = max(2, n // 2)
    rings = [_ring(0.5 * math.pi * i / h, r, 2 * n) @ rot.T + b for i in range(1, h + 1)]
    rings += [_ring(0.5 * math.pi + 0.5 * math.pi * i / h, r, 2 * n) @ rot.T + a for i in range(0, h)]
    return _rings(b + r * y, rings, a - r * y)


def _hull(points: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    pts = np.unique(np.round(points, 7), axis=0)
    if len(pts) < 3:
        return pts, np.zeros((0, 3), dtype=np.int64)
    try:
        from scipy.spatial import ConvexHull
        h = ConvexHull(pts, qhull_options='QJ')
        tris = h.simplices.astype(np.int64)
        c = pts.mean(axis=0)
        for k, (i, j, l) in enumerate(tris):            # outward winding
            nrm = np.cross(pts[j] - pts[i], pts[l] - pts[i])
            if np.dot(nrm, pts[i] - c) < 0:
                tris[k] = (i, l, j)
        return pts, tris
    except Exception:  # noqa: BLE001 - flat or degenerate point set: a fan
        return pts, np.array([[0, i, i + 1] for i in range(1, len(pts) - 1)], dtype=np.int64)


def shape_mesh(s: Shape, segments: int = 8) -> Tuple[np.ndarray, np.ndarray]:
    """Local triangles of a convex shape (polytope faces fanned, capsule and sphere rebuilt from their radius, other
    convex shapes as their hull)."""
    if s.kind == 'sphere' and len(s.vertices):
        return _sphere(s.vertices[0], s.convex_radius, segments)
    if s.kind == 'capsule':
        return _capsule(s.a, s.b, s.convex_radius, segments)
    if s.kind == 'polytope':
        tris = []
        for i in range(len(s.faces)):
            idx = s.face_vertices(i)
            if len(idx) < 3:
                continue
            for k in range(1, len(idx) - 1):
                tri = [idx[0], idx[k], idx[k + 1]]
                if i < len(s.planes):
                    p = s.vertices[tri]
                    if np.dot(np.cross(p[1] - p[0], p[2] - p[0]), s.planes[i][:3]) < 0:
                        tri = [tri[0], tri[2], tri[1]]
                tris.append(tri)
        return s.vertices.copy(), np.array(tris, dtype=np.int64).reshape(-1, 3)
    if s.kind == 'convex':
        return _hull(s.vertices)
    return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)


def meshes(s: Optional[Shape], matrix: np.ndarray, label: str, segments: int = 8,
           out: Optional[list] = None) -> List[Tuple[str, np.ndarray, np.ndarray]]:
    """(label, world vertices, triangles) of every convex leaf of a shape tree placed by matrix."""
    out = [] if out is None else out
    if s is None:
        return out
    if s.kind == 'compound':
        for i, (m, scale, child) in enumerate(s.instances):
            sm = np.identity(4)
            sm[0, 0], sm[1, 1], sm[2, 2] = scale
            meshes(child, matrix @ m @ sm, '{}/{}'.format(label, i), segments, out)
        return out
    v, t = shape_mesh(s, segments)
    if len(t):
        w = np.c_[v, np.ones(len(v))] @ matrix.T
        out.append((label, w[:, :3], t))
    return out


def scene_meshes(data: bytes, segments: int = 8) -> List[Tuple[str, np.ndarray, np.ndarray]]:
    """Every body of a PFX file or physics packfile as world triangles, then its hkGeometry objects."""
    from .ff_pfx import MAGIC as PFX_MAGIC, packfile_bytes
    pf = H.Packfile(packfile_bytes(data) if data[:4] == PFX_MAGIC else data)
    r = Reader(pf)
    out = []
    for i, b in enumerate(r.bodies()):
        meshes(b.shape, b.matrix(), b.name or 'body_{}'.format(i), segments, out)
    out += r.geometries()
    return out


def summary_lines(pf: H.Packfile) -> List[str]:
    """Havok tab: the bodies and their shapes."""
    r = Reader(pf)
    try:
        bodies = r.bodies()
    except Exception as exc:  # noqa: BLE001
        return ['collision shapes: not decoded ({}: {})'.format(type(exc).__name__, exc)]
    if not bodies and not r.shapes:
        return []
    kinds: Dict[str, int] = {}
    for s in r.shapes.values():
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    lines = ['collision: {} bodies, {} shapes ({})'.format(len(bodies), len(r.shapes), ', '.join(
        '{} {}'.format(n, k) for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])))]
    for b in bodies[:50]:
        lines.append('  body {!r}: position {} ; shape {}'.format(
            b.name, ' '.join('{:.3f}'.format(x) for x in b.position),
            b.shape.class_name if b.shape is not None else None))
    if len(bodies) > 50:
        lines.append('  ... {} more bodies'.format(len(bodies) - 50))
    return lines


# ---------------------------------------------------------------------------------------------------------
# Decoded members for the Havok tab (ff_havok.decode)
# ---------------------------------------------------------------------------------------------------------

def _v3(v) -> str:
    return ' '.join('{:.4g}'.format(float(x)) for x in v)


def decode_shape(pf: H.Packfile, o: H.HkObject) -> dict:
    s = Reader(pf).shape(o.offset)
    out = {'kind': s.kind, 'convexRadius': s.convex_radius}
    if s.kind in ('polytope', 'capsule', 'sphere', 'convex'):
        v = s.vertices
        out['vertices'] = '{} (bounding box {} to {})'.format(len(v), _v3(v.min(0)), _v3(v.max(0))) if len(v) else '0'
        out['vertexList'] = [_v3(p) for p in v]
    if s.kind in ('polytope', 'capsule'):
        e = polytope_errors(s)
        out['faces'] = ['{} vertices: {} ; plane {}'.format(n, ' '.join(str(int(i)) for i in s.face_vertices(k)),
                                                           _v3(s.planes[k]) if k < len(s.planes) else '?')
                        for k, (_f, n) in enumerate(s.faces)]
        out['check'] = ('face vertices within {:.2g} of their plane ; vertices at most {:.2g} outside a plane ; '
                        'normals within {:.2g} of 1{}'.format(e['on_plane'], e['outside'], e['normal'],
                                                             '' if not e['range'] else ' ; INDICES OUT OF RANGE'))
    if s.kind == 'capsule':
        out['a'], out['b'], out['radius'] = _v3(s.a), _v3(s.b), s.convex_radius
    if s.kind == 'sphere' and len(s.vertices):
        out['center'], out['radius'] = _v3(s.vertices[0]), s.convex_radius
    if s.kind == 'compound':
        out['aabb'] = '{} to {}'.format(_v3(pf.floats(o.offset + 0x80, 3)), _v3(pf.floats(o.offset + 0x90, 3)))
        out['instances'] = ['translation {} ; scale {} ; {}{}'.format(
            _v3(m[:3, 3]), _v3(sc), c.class_name if c is not None else None,
            '' if np.allclose(m[:3, :3], np.identity(3), atol=1e-6) else ' ; rotated') for m, sc, c in s.instances]
    return out


def decode_system(pf: H.Packfile, o: H.HkObject) -> dict:
    out = {'name': pf.cstr(o.offset + 0x70)}
    for name, off in (('materials', 0x10), ('motionProperties', 0x20), ('motionCinfos', 0x30),
                      ('constraintCinfos', 0x50), ('referencedObjects', 0x60)):
        out[name] = '{} elements'.format(pf.array(o.offset + off)[1])
    t, n = pf.array(o.offset + 0x40)
    r = Reader(pf)
    out['bodyCinfos'] = []
    for i in range(n if t is not None else 0):
        e = t + BODY_SIZE * i
        shape = pf.object_at(pf.ptr(e))
        out['bodyCinfos'].append({'name': pf.cstr(e + 0x20), 'position': _v3(pf.floats(e + 0x30, 3)),
                                  'orientation': _v3(pf.floats(e + 0x40, 4)), 'shape': shape})
    return out


DECODERS = dict([(c, decode_shape) for c in CONVEX_CLASSES | COMPOUND_CLASSES] +
                [('hknpPhysicsSystemData', decode_system)])
