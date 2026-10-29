"""
Fabrique de packfiles Havok 2014 pour les tests (aucun fichier du jeu n'est versionne).

Meme disposition que les fichiers de COTW (journal de recherche, 2026-10-09) : en-tete de 0x40 octets, trois
sections (__classnames__, __types__, __data__), objets du __data__ reperes par les correctifs virtuels, pointeurs par
les correctifs locaux (vers des donnees de la section) et globaux (vers un autre objet).
"""
import struct
from typing import Dict, List, Tuple

from deca.hashes import hash32_func


def bone_hash16(name: str) -> int:
    return int(hash32_func(name.encode())) & 0xFFFF


class Data:
    """Section __data__ en construction : octets, correctifs."""

    def __init__(self):
        self.buf = bytearray()
        self.local: List[Tuple[int, int]] = []
        self.glob: List[Tuple[int, int]] = []
        self.virtual: List[Tuple[int, str]] = []

    def align(self, n=16):
        while len(self.buf) % n:
            self.buf += b'\0'

    def here(self) -> int:
        return len(self.buf)

    def put(self, off: int, fmt: str, *values):
        struct.pack_into(fmt, self.buf, off, *values)

    def reserve(self, n: int) -> int:
        off = self.here()
        self.buf += b'\0' * n
        return off

    def string(self, text: str) -> int:
        off = self.here()
        self.buf += text.encode() + b'\0'
        return off

    def array(self, at: int, target: int, count: int):
        if count:
            self.local.append((at, target))
        self.put(at + 8, '<iI', count, count | 0x80000000)


def build(objects_fn) -> bytes:
    """objects_fn(data) -> list of (offset, class name): place les objets dans data et renvoie leurs debuts."""
    d = Data()
    objs = objects_fn(d)
    d.align()
    classes = []
    for _o, c in objs:
        if c not in classes:
            classes.append(c)
    # __classnames__
    cn = bytearray()
    cn_off: Dict[str, int] = {}
    for c in ['hkClass', 'hkClassMember', 'hkClassEnum', 'hkClassEnumItem'] + classes:
        cn += struct.pack('<IB', 0x12345678, 9)
        cn_off[c] = len(cn)
        cn += c.encode() + b'\0'
    while len(cn) % 16:
        cn += b'\xff'
    # correctifs a la fin du __data__
    data = bytearray(d.buf)
    local_off = len(data)
    for src, dst in d.local:
        data += struct.pack('<ii', src, dst)
    while len(data) % 16:
        data += b'\xff'
    global_off = len(data)
    for src, dst in d.glob:
        data += struct.pack('<iii', src, 2, dst)
    while len(data) % 16:
        data += b'\xff'
    virtual_off = len(data)
    for o, c in objs:
        data += struct.pack('<iii', o, 0, cn_off[c])
    while len(data) % 16:
        data += b'\xff'
    end = len(data)

    header = bytearray(0x40)
    struct.pack_into('<8sii4B5i', header, 0, b'\x57\xE0\xE0\x57\x10\xC0\xC0\x10', 0, 11, 8, 1, 0, 1, 3, 2, 0, 0, 0x4b)
    header[40:56] = b'hk_2014.1.0-r1'.ljust(16, b'\0')
    struct.pack_into('<iHH', header, 56, 0, 21, 0)
    cn_start = 0x40 + 3 * 0x40
    data_start = cn_start + len(cn)

    def section(name, start, offsets):
        s = bytearray(0x40)
        s[:len(name)] = name.encode()
        struct.pack_into('<7i', s, 20, start, *offsets)
        return s
    n = len(cn)
    sections = (section('__classnames__', cn_start, (n, n, n, n, n, n)) +
                section('__types__', data_start, (0, 0, 0, 0, 0, 0)) +
                section('__data__', data_start, (local_off, global_off, virtual_off, end, end, end)))
    return bytes(header + sections + cn + data)


def _quat_largest(q):
    q = [float(v) for v in q]
    i = max(range(4), key=lambda k: abs(q[k]))
    if q[i] < 0:
        q = [-v for v in q]          # q et -q sont la meme rotation : la plus grande composante devient positive
    return i, [v for k, v in enumerate(q) if k != i], q[i] < 0


def quat_threecomp40(q) -> bytes:
    import math
    i, abc, _neg = _quat_largest(q)
    v = 0
    for k, c in enumerate(abc):
        v |= (int(round(c * 2047.0 * math.sqrt(2.0))) + 2047) << (12 * k)
    v |= i << 36
    return v.to_bytes(5, 'little')


def quat_threecomp48(q) -> bytes:
    import math
    i, abc, _neg = _quat_largest(q)
    x, y, z = (int(round(c * 16383.0 * math.sqrt(2.0))) + 16383 for c in abc)
    x |= (i & 1) << 15
    y |= ((i >> 1) & 1) << 15
    return struct.pack('<3H', x, y, z)


def float_mask(spec) -> int:
    """Masque d'une piste de flottants : 0x03 statique, 0x12 spline sur 16 bits (les seuls vus dans COTW)."""
    return 0x03 if spec[0] == 'static' else 0x12


def encode_floats(floats, debut: int) -> bytes:
    """Donnees des pistes de flottants d'un bloc, ecrites a l'offset debut du bloc (alignements relatifs au bloc).
    floats : ('static', valeur) | ('spline', degre, noeuds, valeurs)."""
    body = bytearray()

    def align(n):
        while (debut + len(body)) % n:
            body.append(0)
    for f in floats:
        if f[0] == 'static':
            body += struct.pack('<f', f[1])
        else:
            _k, degree, knots, values = f
            body += struct.pack('<HB', len(values) - 1, degree) + bytes(knots)
            align(4)
            lo, hi = min(values), max(values)
            body += struct.pack('<2f', lo, hi)
            for v in values:
                body += struct.pack('<H', int(round((v - lo) / (hi - lo) * 65535)) if hi > lo else 0)
            align(4)
    return bytes(body)


def encode_block(tracks, n_floats: int = 0, float_masks: bytes = b'') -> bytes:
    """Bloc de hkaSplineCompressedAnimation (disposition : deca/ff_havok_anim.py). tracks : liste de dicts
    position : None | ('static', {axe: valeur}) | ('spline', degre, noeuds, points (n + 1) x 3, toutes les composantes) ;
    rotation : None | ('static', q, quantification) | ('spline', degre, noeuds, quaternions, quantification) ;
    quantification 5 (non compresse), 1 (THREECOMP40) ou 2 (THREECOMP48). Positions sur 16 bits, pas d'echelle."""
    masks = bytearray()
    body = bytearray()

    def align(n):
        while (len(masks_size_hint) + len(body)) % n:
            body.append(0)

    # masques des pistes d'os, un octet par piste de flottants, puis les donnees a partir d'un multiple de 4
    masks_size_hint = b'\0' * (4 * len(tracks) + n_floats + (-(4 * len(tracks) + n_floats)) % 4)

    def quat(q, quant):
        if quant == 5:
            return struct.pack('<4f', *q)
        return quat_threecomp40(q) if quant == 1 else quat_threecomp48(q)

    for t in tracks:
        pos, rot = t.get('position'), t.get('rotation')
        quant = rot[-1] if rot else 5
        pt = rt = 0
        if pos and pos[0] == 'static':
            pt = sum(1 << a for a in pos[1])
            for a in sorted(pos[1]):
                body += struct.pack('<f', pos[1][a])
        elif pos and pos[0] == 'spline':
            _k, degree, knots, points = pos
            pt = 0x70
            body += struct.pack('<HB', len(points) - 1, degree) + bytes(knots)
            align(4)
            lo = [min(p[a] for p in points) for a in range(3)]
            hi = [max(p[a] for p in points) for a in range(3)]
            for a in range(3):
                body += struct.pack('<2f', lo[a], hi[a])
            for p in points:
                for a in range(3):
                    span = hi[a] - lo[a]
                    body += struct.pack('<H', int(round((p[a] - lo[a]) / span * 65535)) if span else 0)
            align(4)
        if rot and rot[0] == 'static':
            rt = 0x0F
            align({5: 4, 1: 1, 2: 2}[quant])
            body += quat(rot[1], quant)
            align(4)
        elif rot and rot[0] == 'spline':
            _k, degree, knots, quats, _q = rot
            rt = 0xF0
            body += struct.pack('<HB', len(quats) - 1, degree) + bytes(knots)
            align({5: 4, 1: 1, 2: 2}[quant])
            for q in quats:
                body += quat(q, quant)
            align(4)
        masks += bytes([1 | (quant << 2) | (1 << 6), pt, rt, 0])
    masks += (float_masks or b'\x03' * n_floats)[:n_floats]
    masks += b'\0' * ((-len(masks)) % 4)
    return bytes(masks) + bytes(body)


def skeleton_and_animation(bones=('Root', 'Spine'), parents=(-1, 0), poses=None, spline=None) -> bytes:
    """Racine -> conteneur d'animation -> squelette, animation spline (une piste d'annotation par os), liaison
    (pistes -> hash16 du nom d'os, particularite d'APEX). poses : par os, (translation, rotation xyzw, echelle) ;
    par defaut translation (i, 0.5, 0), sans rotation ni echelle. spline : donnees de l'animation, dict frames,
    per_block (images par bloc), blocks (liste de listes de pistes pour encode_block, une piste par os)."""
    def objects(d: Data):
        objs = []
        # hkRootLevelContainer
        root = d.reserve(16)
        nv = d.reserve(24)
        d.array(root, nv, 1)
        d.local.append((nv, d.string('Animation Container')))
        d.local.append((nv + 8, d.string('hkaAnimationContainer')))
        d.align()
        objs.append((root, 'hkRootLevelContainer'))
        # hkaAnimationContainer
        cont = d.reserve(96)
        refs = d.reserve(24)
        d.array(cont + 16, refs, 1)
        d.array(cont + 32, refs + 8, 1)
        d.array(cont + 48, refs + 16, 1)
        d.align()
        d.glob.append((nv + 16, cont))
        objs.append((cont, 'hkaAnimationContainer'))
        # hkaSkeleton
        sk = d.reserve(136)
        d.local.append((sk + 16, d.string('Reference')))
        d.align()
        par = d.reserve(2 * len(bones))
        for i, p in enumerate(parents):
            d.put(par + 2 * i, '<h', p)
        d.array(sk + 24, par, len(bones))
        d.align()
        bt = d.reserve(16 * len(bones))
        d.array(sk + 40, bt, len(bones))
        for i, b in enumerate(bones):
            d.local.append((bt + 16 * i, d.string(b)))
        d.align()
        pose = d.reserve(48 * len(bones))
        for i in range(len(bones)):
            if poses is None:
                d.put(pose + 48 * i, '<12f', float(i), 0.5, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0)
            else:
                t, r, sc = poses[i]
                d.put(pose + 48 * i, '<12f', *t, 0, *r, *sc, 0)
        d.array(sk + 56, pose, len(bones))
        d.glob.append((refs, sk))
        objs.append((sk, 'hkaSkeleton'))
        # hkaSplineCompressedAnimation
        an = d.reserve(176)
        d.put(an + 16, '<IfII', 3, 1.5, len(bones), 0)
        d.put(an + 56, '<4I3f', 46, 1, 256, 4, 1.5, 1 / 1.5, 1 / 30)
        if spline is not None:
            flottants = spline.get('float_blocks') or [[('static', 0.0)] * spline.get('floats', 0)
                                                        for _b in spline['blocks']]
            n_floats = len(flottants[0])
            blocs = [encode_block(b, n_floats, bytes(float_mask(f) for f in fl))
                     for b, fl in zip(spline['blocks'], flottants)]
            per = spline['per_block']
            d.put(an + 20, '<f', (spline['frames'] - 1) / 30)
            d.put(an + 28, '<I', n_floats)
            d.put(an + 56, '<4I3f', spline['frames'], len(blocs), per, 4 * len(bones) + n_floats, (per - 1) / 30,
                  30 / (per - 1), 1 / 30)
            d.align()
            data = d.here()
            debuts, fins = [], []
            for blk, fl in zip(blocs, flottants):
                debuts.append(d.here() - data)
                fins.append(len(blk))
                d.buf += blk + encode_floats(fl, len(blk))
                d.align()
            taille = d.here() - data
            d.array(an + 152, data, taille)
            offs = d.reserve(4 * len(blocs))
            for i, v in enumerate(debuts):
                d.put(offs + 4 * i, '<I', v)
            d.array(an + 88, offs, len(blocs))
            ends = d.reserve(4 * len(blocs))
            for i, v in enumerate(fins):
                d.put(ends + 4 * i, '<I', v)
            d.array(an + 104, ends, len(blocs))
            d.align()
        tracks = d.reserve(24 * len(bones))
        d.array(an + 40, tracks, len(bones))
        for i, b in enumerate(bones):
            d.local.append((tracks + 24 * i, d.string(b)))
        d.align()
        d.glob.append((refs + 8, an))
        objs.append((an, 'hkaSplineCompressedAnimation'))
        # hkaAnimationBinding
        bi = d.reserve(96)
        d.glob.append((bi + 24, an))
        idx = d.reserve(2 * len(bones))
        for i, b in enumerate(bones):
            d.put(idx + 2 * i, '<H', bone_hash16(b))
        d.array(bi + 32, idx, len(bones))
        d.align()
        d.glob.append((refs + 16, bi))
        objs.append((bi, 'hkaAnimationBinding'))
        return objs
    return build(objects)


def navmesh_and_physics() -> bytes:
    """Racine -> maillage de navigation (3 sommets, 1 face, 3 aretes) et un objet d'une classe sans decodeur (lu
    par ses correctifs : chaine, tableau, reference)."""
    def objects(d: Data):
        objs = []
        root = d.reserve(16)
        nv = d.reserve(48)
        d.array(root, nv, 2)
        for i, (name, cls) in enumerate((('hkaiNavMesh', 'hkaiNavMesh'), ('Physics Data', 'hkpPhysicsData'))):
            d.local.append((nv + 24 * i, d.string(name)))
            d.local.append((nv + 24 * i + 8, d.string(cls)))
        d.align()
        objs.append((root, 'hkRootLevelContainer'))
        # hkaiNavMesh
        nm = d.reserve(160)
        d.put(nm + 112, '<ii', 1, 0)
        faces = d.reserve(16)
        d.array(nm + 16, faces, 1)
        edges = d.reserve(32 * 3)
        d.array(nm + 32, edges, 3)
        verts = d.reserve(16 * 3)
        for i, v in enumerate(((-10.0, 0.0, 5.0), (20.0, 1.0, 5.0), (0.0, 2.5, -7.0))):
            d.put(verts + 16 * i, '<4f', *v, 0.0)
        d.array(nm + 48, verts, 3)
        d.glob.append((nv + 16, nm))
        objs.append((nm, 'hkaiNavMesh'))
        # objet sans decodeur : +0x10 nom, +0x18 tableau de 4 u32, +0x28 reference vers le maillage
        ph = d.reserve(48)
        d.local.append((ph + 16, d.string('physics_test')))
        d.align()
        arr = d.reserve(16)
        d.put(arr, '<4I', 1, 2, 3, 4)
        d.array(ph + 24, arr, 4)
        d.glob.append((ph + 40, nm))
        d.glob.append((nv + 40, ph))
        objs.append((ph, 'hkpPhysicsData'))
        return objs
    return build(objects)


def btc_bytes(params=(('fly_preset', 7), ('flee_distance', 120)), strings=('ROAMING FAILED PATH', 'ACT_CALL'),
              source='intermediate/ai/bts/animals/test_flee.bts') -> bytes:
    """Arbre de comportement compile (.btc) : en-tete de 12 u32, code (chaines terminees par NUL), bloc de fin de
    0x30 octets, table des parametres (20 octets par entree), leurs valeurs, puis le nom source (fin du fichier)."""
    code = bytearray(b'\x01\x00\x02\x00')
    for s in strings:
        code += s.encode() + b'\0'
    while len(code) % 4:
        code += b'\0'
    end_block = 0x30 + len(code)
    table = end_block + 0x30
    entries = b''.join(struct.pack('<IHHIII', int(hash32_func(n.encode())), i, 1, 8 * i, 0, 2)
                       for i, (n, _v) in enumerate(params))
    values_off = table + len(entries)
    values = b''.join(struct.pack('<I', v) for _n, v in params)
    name_off = values_off + len(values)
    name = source.encode()
    header = struct.pack('<12I', 0x12, 0x30, end_block, table if params else 0, len(entries),
                         values_off if params else 0, len(params), name_off, len(name), 0xA1B2C3D4, 3, 0xFFFFFFFF)
    trailer = struct.pack('<24H', *range(24))
    return header + bytes(code) + trailer + entries + values + name + b'\0'


# ---------------------------------------------------------------------------------------------------------
# Formes de collision Havok Physics 2014 (hknp), disposition de deca/ff_havok_physics.py
# ---------------------------------------------------------------------------------------------------------

BOITE_PLANS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


def _boite(demi):
    """Sommets, plans et faces d'une boite centree de demi-cotes donnes ; les sommets d'une face sont dans l'ordre du
    contour (comme dans les fichiers du jeu, ou l'eventail des faces donne un maillage ferme), sens direct vu de
    l'exterieur."""
    import math
    hx, hy, hz = demi
    verts = [(sx * hx, sy * hy, sz * hz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    plans, faces = [], []
    for n in BOITE_PLANS:
        d = -abs(n[0] * hx + n[1] * hy + n[2] * hz)
        plans.append((n[0], n[1], n[2], d))
        idx = [i for i, v in enumerate(verts) if abs(v[0] * n[0] + v[1] * n[1] + v[2] * n[2] + d) < 1e-9]
        k = [a for a in range(3) if n[a] == 0]                  # les deux axes du plan de la face
        sens = 1 if sum(n) > 0 else -1
        if k == [0, 2]:                                         # y : (z, x) est direct
            k = [2, 0]
        idx.sort(key=lambda i: sens * math.atan2(verts[i][k[1]], verts[i][k[0]]))
        faces.append(idx)
    return verts, plans, faces


def _rel(d: Data, champ: int, cible: int, n: int):
    d.put(champ, '<HH', n, (cible - champ) if n else 0)


def _convexe(d: Data, verts, plans=None, faces=None, rayon=0.01, fixe=0x50, extra=None) -> int:
    """Objet hknpConvexShape / hknpConvexPolytopeShape : partie fixe, puis sommets, plans, faces, indices (tableaux
    relatifs) ; extra(off) remplit la partie fixe au-dela de 0x50 (capsule : a, b)."""
    off = d.reserve(fixe)
    d.put(off + 0x10, '<HBBf', 0x143, 0, 1, rayon)
    if extra:
        extra(off)
    d.align()
    vt = d.reserve(16 * len(verts))
    for i, v in enumerate(verts):
        d.put(vt + 16 * i, '<3fI', *v, 0x3f000000 | i)
    _rel(d, off + 0x30, vt, len(verts))
    if plans is not None:
        pt = d.reserve(16 * len(plans))
        for i, p in enumerate(plans):
            d.put(pt + 16 * i, '<4f', *p)
        _rel(d, off + 0x40, pt, len(plans))
        ft = d.reserve(4 * len(faces))
        idx, first = [], 0
        for i, f in enumerate(faces):
            d.put(ft + 4 * i, '<HBB', first, len(f), 0)
            idx += f
            first += len(f)
        _rel(d, off + 0x44, ft, len(faces))
        it = d.reserve(len(idx))
        d.buf[it:it + len(idx)] = bytes(idx)
        _rel(d, off + 0x48, it, len(idx))
    d.align()
    return off


def physique(pfx: bool = True) -> bytes:
    """Un corps (nom, position (10, 0, 5), quart de tour autour de y) dont la forme est composee de trois instances :
    une boite 1 x 2 x 1 a l'origine ; une capsule (0, 0, 0) - (0, 1, 0) de rayon 0,25, couchee le long de +z par un
    quart de tour autour de x, en (0, 3, 0) ; la boite tournee d'un quart de tour autour de z, en (3, 0, 0). Plus une
    sphere de rayon 0,5 hors de tout corps. Boite englobante de la forme composee : exacte (feuilles, rayons compris) ;
    lue avec les rotations transposees, la capsule en sortirait (vers -z). pfx : enveloppe PFX (en-tete 0x44)."""
    import math

    def objects(d: Data):
        objs = []
        systeme = d.reserve(0x80)
        objs.append((systeme, 'hknpPhysicsSystemData'))
        corps = d.reserve(0x60)
        d.array(systeme + 0x40, corps, 1)
        d.local.append((corps + 0x20, d.string('bloc_test_COL')))
        d.local.append((systeme + 0x70, d.string('Default Physics System Data')))
        d.align()
        s = math.sqrt(0.5)
        d.put(corps + 0x30, '<4f', 10.0, 0.0, 5.0, 1.0)
        d.put(corps + 0x40, '<4f', 0.0, s, 0.0, s)
        # forme composee
        comp = d.reserve(0xa0)
        objs.append((comp, 'hknpStaticCompoundShape'))
        inst = d.reserve(0x80 * 3)
        d.array(comp + 0x60, inst, 3)
        matrices = [((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0)),
                    ((1, 0, 0), (0, 0, 1), (0, -1, 0), (0, 3, 0)),          # colonnes : y -> z, z -> -y
                    ((0, 1, 0), (-1, 0, 0), (0, 0, 1), (3, 0, 0))]          # colonnes : x -> y, y -> -x
        for i, cols in enumerate(matrices):
            for k, c in enumerate(cols):
                d.put(inst + 0x80 * i + 16 * k, '<3fI', *c, 0x3f000000)
            d.put(inst + 0x80 * i + 0x40, '<4f', 1.0, 1.0, 1.0, 1.0)
        d.put(comp + 0x80, '<4f', -0.51, -1.01, -0.51, 0.0)    # boites : rayon convexe 0,01 en plus
        d.put(comp + 0x90, '<4f', 4.01, 3.25, 1.25, 0.0)       # boite tournee : x jusqu'a 4 ; capsule : z jusqu'a 1,25
        # boite 1 x 2 x 1
        verts, plans, faces = _boite((0.5, 1.0, 0.5))
        boite = _convexe(d, verts, plans, faces, rayon=0.01)
        objs.append((boite, 'hknpConvexPolytopeShape'))
        # capsule : polytope d'une boite fine autour du segment, a et b en +0x50 et +0x60
        cv, cp, cf = _boite((0.001, 0.5, 0.001))
        cv = [(x, y + 0.5, z) for x, y, z in cv]
        cp = [(n0, n1, n2, dd - n1 * 0.5) for n0, n1, n2, dd in cp]
        caps = _convexe(d, cv, cp, cf, rayon=0.25, fixe=0x70,
                        extra=lambda o: (d.put(o + 0x50, '<4f', 0.0, 0.0, 0.0, 0.0), d.put(o + 0x60, '<4f', 0.0, 1.0, 0.0, 0.0)))
        objs.append((caps, 'hknpCapsuleShape'))
        sphere = _convexe(d, [(0.0, 0.0, 0.0)] * 4, rayon=0.5)
        objs.append((sphere, 'hknpSphereShape'))
        d.glob += [(corps, comp), (inst + 0x50, boite), (inst + 0x80 + 0x50, caps), (inst + 0x100 + 0x50, boite)]
        return objs
    pack = build(objects)
    if not pfx:
        return pack
    return b'PFX\0' + struct.pack('<3I', 1, 1, 3) + bytes(0x44 - 16) + pack
