#!/usr/bin/env python3
"""
Tests des formats de sommets compresses sur 32 bits (deca/ff_adf_amf.py : R10G10B10A2_UNORM, R10G10B10A2_UINT,
R11G11B10_FLOAT) et des controles de cotw/audit_maillages.py.

Les maillages sont fabriques directement en objets Amf* (aucun maillage du jeu n'est versionne) et passent par la
vraie conversion de DECA (amf_meshc_reformat), comme dans l'export glTF.

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_maillages.py
"""
import struct
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _projet  # noqa: E402,F401
import audit_maillages as AM  # noqa: E402
from deca import ff_adf_amf as A  # noqa: E402


def attr(usage, fmt, offset, stride, index=0):
    a = A.AmfStreamAttribute()
    a.usage = (0, usage)
    a.format = (0, fmt)
    a.streamIndex = index
    a.streamOffset = offset
    a.streamStride = stride
    a.packingData = (0.0, 0.0)
    return a


def maillage(n_sommets, attrs, strides, indices, offsets=None):
    m = A.AmfMesh()
    m.vertexCount = n_sommets
    m.indexCount = len(indices)
    m.indexBufferIndex = 0
    m.indexBufferStride = 2
    m.indexBufferOffset = 0
    m.vertexBufferIndices = list(range(len(strides)))
    m.vertexStreamStrides = list(strides)
    m.vertexStreamOffsets = offsets or [0] * len(strides)
    m.meshProperties = {}
    m.boneIndexLookup = []
    sm = A.AmfSubMesh()
    sm.subMeshId = b'test'
    sm.indexCount = len(indices)
    sm.indexStreamOffset = 0
    m.subMeshes = [sm]
    m.streamAttributes = attrs
    return m


def en_tete(m):
    lg = A.AmfLodGroup()
    lg.lodIndex = 0
    lg.meshes = [m]
    h = A.AmfMeshHeader()
    h.lodGroups = [lg]
    h.highLodPath = b''
    return h


def tampons(index_bytes, vertex_buffers):
    b = A.AmfMeshBuffers()
    ib = A.AmfBuffer()
    ib.data = index_bytes
    b.indexBuffers = [ib]
    b.vertexBuffers = []
    for d in vertex_buffers:
        vb = A.AmfBuffer()
        vb.data = d
        b.vertexBuffers.append(vb)
    return b


class TestFormats(unittest.TestCase):
    def test_r10g10b10a2_unorm(self):
        packed = np.array([0x3ff | (0 << 10) | (512 << 20) | (3 << 30), 0], dtype=np.uint32)
        out = np.zeros((2, 4), dtype=np.float32)
        A.convert_R10G10B10A2_UNORM(out, packed, None, False)
        np.testing.assert_allclose(out[0], [1.0, 0.0, 512 / 1023, 1.0], rtol=1e-6)
        back = np.zeros(2, dtype=np.uint32)
        A.convert_R10G10B10A2_UNORM(back, out, None, True)
        self.assertEqual(list(back), list(packed))

    def test_r10g10b10a2_uint(self):
        rng = np.random.default_rng(1)
        packed = rng.integers(0, 1 << 32, size=500, dtype=np.uint64).astype(np.uint32)
        comps = np.zeros((500, 4), dtype=np.uint16)
        A.convert_R10G10B10A2_UINT(comps, packed, None, False)
        self.assertLessEqual(int(comps[:, :3].max()), 1023)
        self.assertLessEqual(int(comps[:, 3].max()), 3)
        back = np.zeros(500, dtype=np.uint32)
        A.convert_R10G10B10A2_UINT(back, comps, None, True)
        self.assertTrue(np.array_equal(back, packed))

    def test_r11g11b10_known_values(self):
        one = (15 << 6) | ((15 << 6) << 11) | ((15 << 5) << 22)          # 1.0, 1.0, 1.0
        half = (14 << 6) | (((15 << 6) | 32) << 11) | ((16 << 5) << 22)  # 0.5, 1.5, 2.0
        out = np.zeros((2, 3), dtype=np.float32)
        A.convert_R11G11B10_FLOAT(out, np.array([one, half], dtype=np.uint32), None, False)
        np.testing.assert_allclose(out, [[1.0, 1.0, 1.0], [0.5, 1.5, 2.0]])

    def test_r11g11b10_same_as_deca_image_decoder(self):
        """Meme decodage que dxgi.process_image_26 (textures R11G11B10_FLOAT de DECA), sur toutes les valeurs finies."""
        from deca import dxgi
        rng = np.random.default_rng(2)
        packed = rng.integers(0, 1 << 32, size=4096, dtype=np.uint64).astype(np.uint32)
        exps = [(packed >> 6) & 0x1f, (packed >> 17) & 0x1f, (packed >> 27) & 0x1f]
        packed = packed[(exps[0] < 31) & (exps[1] < 31) & (exps[2] < 31)]
        out = np.zeros((len(packed), 3), dtype=np.float32)
        A.convert_R11G11B10_FLOAT(out, packed, None, False)
        image = np.zeros((1, len(packed), 4), dtype=np.float32)
        buf = packed.tobytes()
        dxgi.process_image_26(image, buf, len(buf), len(packed), 1)
        np.testing.assert_allclose(out, image[0, :, :3], rtol=1e-6)

    def test_deca_half_float_decoder_matches_numpy(self):
        """dxgi.u16_to_f16_in_f32 (textures R16G16B16A16_FLOAT) = float16 de numpy, denormaux compris."""
        from deca import dxgi
        vals = np.arange(0, 1 << 16, 7, dtype=np.uint16)
        vals = vals[((vals >> 10) & 0x1f) != 31]
        image = np.zeros((1, len(vals) // 4, 4), dtype=np.float32)
        buf = vals[:len(vals) // 4 * 4].tobytes()
        dxgi.process_image_10(image, buf, len(buf), len(vals) // 4, 1)
        np.testing.assert_allclose(image.reshape(-1), vals[:len(vals) // 4 * 4].view(np.float16).astype(np.float32),
                                   rtol=1e-6)

    def test_r11g11b10_round_trip(self):
        rng = np.random.default_rng(3)
        vals = np.concatenate([rng.uniform(0, 4, (300, 3)), rng.uniform(0, 1e-4, (50, 3)), [[0, 0, 0]]])
        packed = np.zeros(len(vals), dtype=np.uint32)
        A.convert_R11G11B10_FLOAT(packed, vals, None, True)
        out = np.zeros((len(vals), 3), dtype=np.float64)
        A.convert_R11G11B10_FLOAT(out, packed, None, False)
        # demi pas de mantisse : 2^-7 (11 bits) et 2^-6 (10 bits) en relatif, 2^-14 / 2^6 en absolu (denormaux)
        tol = np.array([2.0 ** -7, 2.0 ** -7, 2.0 ** -6])
        err = np.abs(out - vals)
        self.assertTrue(np.all(err <= tol * np.abs(vals) + 2.0 ** -20), float(np.max(err / (np.abs(vals) + 1e-30))))
        # negatifs a 0, trop grands au plus grand fini
        A.convert_R11G11B10_FLOAT(packed[:2], np.array([[-1.0, 1e9, np.nan], [0.0, 0.0, 1e9]]), None, True)
        A.convert_R11G11B10_FLOAT(out[:2], packed[:2], None, False)
        self.assertEqual(out[0, 0], 0.0)
        self.assertEqual(out[0, 1], 65024.0)        # 2^15 x (1 + 63/64)
        self.assertEqual(out[0, 2], 0.0)
        self.assertEqual(out[1, 2], 64512.0)        # 2^15 x (1 + 31/32)


class TestUnitVec(unittest.TestCase):
    def test_unit_vec_round_trip(self):
        rng = np.random.default_rng(5)
        v = rng.normal(size=(1000, 3))
        v /= np.linalg.norm(v, axis=1)[:, None]
        f = np.zeros(1000, dtype=np.float32)
        A.convert_R32_UNIT_VEC_AS_FLOAT(f, v, None, True)
        out = np.zeros((1000, 3), dtype=np.float64)
        A.convert_R32_UNIT_VEC_AS_FLOAT(out, f, None, False)
        # x : 7 bits utiles seulement quand z est grand (flottant 32 bits vers 65536 : pas de 2^-7), comme dans le jeu
        self.assertLess(float(np.max(np.abs(out - v))), 3.2 / 256)

    def test_unit_vec_known_value(self):
        f = np.array([128 / 256.0 + 255 + 256.0 * 0], dtype=np.float32)    # c = (128, 255, 0)
        out = np.zeros((1, 3))
        A.convert_R32_UNIT_VEC_AS_FLOAT(out, f, None, False)
        # les octets du dessous debordent sur y et z (moins de 1/256) : y = (255 + 0,5) / 256, z = 255,5 / 65536
        np.testing.assert_allclose(out[0], [0.0, 2 * 255.5 / 256 - 1.0, 2 * 255.5 / 65536 - 1.0], atol=1e-6)


class TestMateriaux(unittest.TestCase):
    """Deca3dModelc.add_to_gltf avec un modele fabrique (AmfModel remplace) : les RenderBlockId releves par
    16-materiaux recoivent un materiau glTF."""

    def exporter(self, materiaux):
        import pygltflib as pyg
        from deca import ff_adf_amf_gltf as G

        class Mat:
            def __init__(self, name, bloc, textures, attrs):
                self.name, self.renderBlockId, self.textures, self.attributes = name, bloc, textures, attrs

        class Modele:
            def __init__(self, *_a):
                self.mesh = b'm.meshc'
                self.materials = [Mat(*m) for m in materiaux]

        class Node:
            file_type = 'adf'

        class Vfs:
            class logger:
                messages = []

                @classmethod
                def log(cls, m, *a):
                    cls.messages.append(str(m))
                warning = log

            def nodes_where_match(self, v_path=None):
                return [Node()]

        class AdfDb:
            def read_node(self, _vfs, _node):
                class A:
                    table_instance_full_values = [None]
                return A()

        class Db:
            def __init__(self):
                self.n = 0

            def gltf_add_texture(self, _g, vp):
                if not vp:
                    return None
                self.n += 1
                return self.n - 1

            def gltf_add_meshc(self, _g, _m):
                return {0: []}

        gltf = pyg.GLTF2()
        ancien = G.AmfModel
        G.AmfModel = Modele
        try:
            G.Deca3dModelc(b'x.modelc').add_to_gltf(Vfs(), AdfDb(), Db(), gltf)
        finally:
            G.AmfModel = ancien
        return gltf, Vfs.logger.messages

    def test_new_render_blocks_get_a_material(self):
        tex = [b'a_dif.ddsc', b'a_nrm.ddsc', b'a_mpm.ddsc', b'', b'a_rgb.ddsc']
        gltf, logs = self.exporter([
            (b'peau', b'CharacterSkin', tex, {'DoubleSided': True}),
            (b'arme', b'WeaponCamo', tex, {}),
            (b'vitre', b'Window', tex, {}),
            (b'carbone', b'GeneralMkIII', [b'c_dif.ddsc', b'c_glo.ddsc', b'c_met.ddsc', b'c_nrm.ddsc'], {}),
            (b'inconnu', b'Truc', tex, {}),
        ])
        mats = {m.name: m for m in gltf.materials}
        self.assertEqual(set(mats), {'peau', 'arme', 'vitre', 'carbone'})
        self.assertTrue(mats['peau'].doubleSided)
        self.assertEqual(mats['peau'].pbrMetallicRoughness.baseColorTexture.index, 0)
        self.assertEqual(mats['peau'].normalTexture.index, 1)
        self.assertEqual(mats['arme'].emissiveFactor, [0.0, 0.0, 0.0])      # [4] = masque de camouflage
        self.assertEqual(mats['vitre'].alphaMode, 'BLEND')
        self.assertIsNotNone(mats['carbone'].normalTexture)
        self.assertTrue(any('Unhandled RenderBlockId' in m and 'Truc' in m for m in logs))


class TestReformat(unittest.TestCase):
    def test_packed_streams_through_deca_reformat(self):
        """Positions R32G32B32 + couleur R10G10B10A2_UNORM + normale R11G11B10_FLOAT + indices d'os
        R10G10B10A2_UINT : la conversion de DECA les rend dans des formats que l'export glTF sait decrire."""
        n = 3
        pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        col = np.array([0x3ff | (3 << 30)] * n, dtype=np.uint32)           # r = 1, a = 1
        nrm = np.array([(15 << 6)] * n, dtype=np.uint32)                    # (1, 0, 0)
        bones = np.array([1 | (2 << 10) | (3 << 20)] * n, dtype=np.uint32)
        stride = 12 + 4 + 4 + 4
        data = b''.join(pos[i].tobytes() + struct.pack('<3I', col[i], nrm[i], bones[i]) for i in range(n))
        attrs = [attr(b'AmfUsage_Position', b'AmfFormat_R32G32B32_FLOAT', 0, stride),
                 attr(b'AmfUsage_Color', b'AmfFormat_R10G10B10A2_UNORM', 12, stride),
                 attr(b'AmfUsage_Normal', b'AmfFormat_R11G11B10_FLOAT', 16, stride),
                 attr(b'AmfUsage_BoneIndex', b'AmfFormat_R10G10B10A2_UINT', 20, stride)]
        m = maillage(n, attrs, [stride], [0, 1, 2])
        h = en_tete(m)
        b = tampons(struct.pack('<3H', 0, 1, 2), [data])
        A.amf_meshc_reformat(h, b)
        formats = {sa.usage[1]: sa.format[1] for sa in m.streamAttributes}
        self.assertTrue(set(formats.values()) <= AM.FORMATS_EXPORT, formats)
        self.assertEqual(formats[b'AmfUsage_Color'], b'AmfFormat_R32G32B32A32_FLOAT')
        self.assertEqual(formats[b'AmfUsage_Normal'], b'AmfFormat_R32G32B32_FLOAT')
        self.assertEqual(formats[b'AmfUsage_BoneIndex'], b'AmfFormat_R16G16B16A16_SINT')
        # relire la couleur et la normale converties
        out = b.vertexBuffers[0].data
        sa = {s.usage[1]: s for s in m.streamAttributes}
        st = sa[b'AmfUsage_Color'].streamStride
        c = np.frombuffer(out[sa[b'AmfUsage_Color'].streamOffset:][:16], dtype=np.float32)
        np.testing.assert_allclose(c, [1.0, 0.0, 0.0, 1.0])
        nv = np.frombuffer(out[st + sa[b'AmfUsage_Normal'].streamOffset:][:12], dtype=np.float32)
        np.testing.assert_allclose(nv, [1.0, 0.0, 0.0])
        bi = np.frombuffer(out[sa[b'AmfUsage_BoneIndex'].streamOffset:][:8], dtype=np.int16)
        self.assertEqual(list(bi), [1, 2, 3, 0])


def sphere(n_lat=12, n_lon=24):
    """Sphere unite : positions, normales (= positions) et triangles dans le sens direct (normales sortantes)."""
    pts = [(0.0, 0.0, 1.0)]
    for i in range(1, n_lat):
        th = np.pi * i / n_lat
        for j in range(n_lon):
            ph = 2 * np.pi * j / n_lon
            pts.append((np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)))
    pts.append((0.0, 0.0, -1.0))
    pts = np.array(pts)
    tri = []
    for j in range(n_lon):
        tri.append((0, 1 + j, 1 + (j + 1) % n_lon))
    for i in range(n_lat - 2):
        for j in range(n_lon):
            a, b = 1 + i * n_lon + j, 1 + i * n_lon + (j + 1) % n_lon
            c, d = a + n_lon, b + n_lon
            tri += [(a, c, d), (a, d, b)]
    last = len(pts) - 1
    base = 1 + (n_lat - 2) * n_lon
    for j in range(n_lon):
        tri.append((base + j, last, base + (j + 1) % n_lon))
    return pts, np.array(tri)


def code_unit_vec(v):
    """Codage candidat : octets c = (v + 1) / 2 x 255, f = c0 / 256 + c1 + 256 c2."""
    c = np.rint((np.asarray(v) + 1.0) / 2.0 * 255.0)
    return (c[:, 0] / 256.0 + c[:, 1] + 256.0 * c[:, 2]).astype(np.float32)


class TestGeometrie(unittest.TestCase):
    def setUp(self):
        import geometrie_maillages as GM
        self.GM = GM
        pts, tri = sphere()
        self.tri = tri
        n = len(pts)
        scale = 2.0
        pos16 = np.rint(pts / scale * 0x7fff).astype(np.int16)
        f = code_unit_vec(pts)
        stride = 6 + 2 + 4
        data = b''.join(pos16[i].tobytes() + b'\0\0' + f[i:i + 1].tobytes() for i in range(n))
        pa = attr(b'AmfUsage_Position', b'AmfFormat_R16G16B16_SNORM', 0, stride)
        pa.packingData = (scale, 0.0)
        na = attr(b'AmfUsage_Normal', b'AmfFormat_R32_UNIT_VEC_AS_FLOAT', 8, stride)
        self.mesh = maillage(n, [pa, na], [stride], list(tri.reshape(-1)))
        self.buffers = tampons(tri.astype(np.uint16).tobytes(), [data])

    def test_positions_and_box(self):
        pos = self.GM.positions(self.mesh, self.buffers.vertexBuffers)
        self.assertLess(float(np.max(np.abs(np.linalg.norm(pos, axis=1) - 1.0))), 1e-3)
        self.assertEqual(self.GM.dans_boite(pos, [-1, -1, -1], [1, 1, 1]), 1.0)
        self.assertLess(self.GM.dans_boite(pos, [-1, -1, -1], [0, 1, 1]), 0.7)

    def test_scores_find_the_right_decoding(self):
        m = self.GM.mesurer(self.mesh, self.buffers.vertexBuffers, self.buffers.indexBuffers,
                            ([-1, -1, -1], [1, 1, 1]))
        self.assertGreater(m['normale:z_corrige_2v-1'], 0.98)
        self.assertLess(m['normale:z_corrige'], 0.9)
        self.assertGreater(m['normale:deca'], 0.98)                 # DECA corrige le 22/10
        f = self.GM._flux(self.mesh, self.buffers.vertexBuffers, self.mesh.streamAttributes[1], 'f4')
        ancien = self.GM.decoder_unit_vec(f, 'deca_avant_22_10')
        self.assertLess(self.GM.score(ancien, self.GM.normales_des_faces(
            self.GM.positions(self.mesh, self.buffers.vertexBuffers), self.tri)), 0.9)
        self.assertEqual(m['boite'], 1.0)
        # faces dans l'autre sens : score negatif
        self.mesh.subMeshes[0].indexCount = len(self.tri) * 3
        inv = tampons(self.tri[:, ::-1].astype(np.uint16).tobytes(), [self.buffers.vertexBuffers[0].data])
        m2 = self.GM.mesurer(self.mesh, inv.vertexBuffers, inv.indexBuffers)
        self.assertLess(m2['normale:z_corrige_2v-1'], -0.98)

    def test_bones(self):
        n = 4
        idx = np.array([[0, 1, 0, 0], [2, 0, 0, 0], [5, 0, 0, 0], [1, 2, 0, 0]], dtype=np.uint8)
        w = np.array([[128, 127, 0, 0], [255, 0, 0, 0], [255, 0, 0, 0], [200, 55, 0, 0]], dtype=np.uint8)
        stride = 8
        data = b''.join(idx[i].tobytes() + w[i].tobytes() for i in range(n))
        attrs = [attr(b'AmfUsage_BoneIndex', b'AmfFormat_R8G8B8A8_UINT', 0, stride),
                 attr(b'AmfUsage_BoneWeight', b'AmfFormat_R8G8B8A8_UNORM', 4, stride)]
        m = maillage(n, attrs, [stride], [0, 1, 2])
        m.boneIndexLookup = [10, 11, 12]
        out = {}
        self.GM.mesurer_os(m, tampons(b'', [data]).vertexBuffers, out)
        self.assertAlmostEqual(out['os_hors_table'], 0.25)        # l'os 5 n'est pas dans une table de 3
        self.assertAlmostEqual(out['poids_ecart'], 0.0)

    def test_cumuler(self):
        c = self.GM.cumuler([{'sommets': 1.0, 'a': 1.0}, {'sommets': 3.0, 'a': 0.0, 'b': 2.0}])
        self.assertAlmostEqual(c['a'], 0.25)
        self.assertAlmostEqual(c['b'], 2.0)


class TestAudit(unittest.TestCase):
    def test_indices(self):
        m = maillage(3, [], [12], [0, 1, 2])
        self.assertIsNone(AM.verifier_indices(m, struct.pack('<3H', 0, 1, 2)))
        self.assertIn('indice 3 >= 3', AM.verifier_indices(m, struct.pack('<3H', 0, 1, 3)))
        self.assertIn('hors du tampon', AM.verifier_indices(m, struct.pack('<2H', 0, 1)))
        m.indexBufferStride = 1
        self.assertIn('1 octets', AM.verifier_indices(m, b'\0\1\2'))

    def test_node_of_type_prefers_the_right_file_on_a_name_collision(self):
        from deca.ff_adf_amf_gltf import node_of_type, TYPES_ADF_NODE, TYPES_TEXTURE_NODE

        class N:
            def __init__(self, uid, ft):
                self.uid, self.file_type = uid, ft

        class V:
            def nodes_where_match(self, v_path=None):
                return [N(1, None), N(2, 'adf'), N(3, 'avtx')] if v_path == b'a.meshc' else []
        self.assertEqual([n.uid for n in node_of_type(V(), b'a.meshc', TYPES_ADF_NODE)], [2, 1, 3])
        self.assertEqual(node_of_type(V(), b'a.meshc', TYPES_TEXTURE_NODE)[0].uid, 3)
        self.assertEqual(node_of_type(V(), b'b.meshc', TYPES_ADF_NODE), [])

    def test_material_survey(self):
        import json
        import tempfile
        self.assertEqual(AM.suffixe_texture('models/w/rifle_01_dif.ddsc'), 'dif')
        self.assertEqual(AM.suffixe_texture(''), '(vide)')
        self.assertEqual(AM.suffixe_texture('models/w/rifle_01.ddsc'), '(autre)')
        mats = [('a.modelc', 'BlocInconnu', ['w/a_dif.ddsc', 'w/a_nrm.ddsc', ''], {'UseCamo': True, 'Scale': 0.5}),
                ('b.modelc', 'BlocInconnu', ['w/b_dif.ddsc', 'w/b_nrm.ddsc', 'w/b_mpm.ddsc'], {'UseCamo': False}),
                ('b.modelc', 'GeneralR2', ['w/c_dif.ddsc'], {})]
        with tempfile.TemporaryDirectory() as d:
            c = AM.ecrire_materiaux(Path(d), mats, 'P', 1.0, 2)
            md = (Path(d) / 'materiaux.md').read_text(encoding='utf-8')
            data = json.loads((Path(d) / 'materiaux.json').read_text(encoding='utf-8'))
        self.assertEqual(c['BlocInconnu'], 2)
        self.assertIn('## BlocInconnu (2 materiaux, 2 modeles, **non traite par l\'export**)', md)
        self.assertIn('| 2 | (vide) 50 %, mpm 50 % |', md)
        self.assertIn('| UseCamo | True (1), False (1) |', md)
        self.assertEqual(data['BlocInconnu']['emplacements']['0']['roles'], {'dif': 2})
        self.assertTrue(data['GeneralR2']['traite'])

    def test_just_cause(self):
        self.assertTrue(AM.herite_just_cause('models/jc_vehicles/02_air/v2102/utility_body.meshc'))
        self.assertTrue(AM.herite_just_cause('jc_props/a.meshc'))
        self.assertFalse(AM.herite_just_cause('models/hp_weapons/jcx.meshc'))      # nom de fichier, pas dossier
        self.assertFalse(AM.herite_just_cause('models/hp_characters/animals/jackal/a.meshc'))

    def test_report(self):
        import tempfile
        rs = []
        for i, (st, det) in enumerate([('ok', ''), ('ok', ''), ('echec_conversion', 'KeyError: x'),
                                       ('format_non_exporte', 'R16G16B16A16_UINT (Color)')]):
            r = AM.Resultat('models/m{}.meshc'.format(i), 100 + i)
            r.statut, r.detail = st, det
            r.formats[('Position', 'R16G16B16_SNORM')] = 1
            rs.append(r)
        rs[2].hr_uid = 900
        with tempfile.TemporaryDirectory() as d:
            AM.ecrire(Path(d), rs, 'D:\\dev\\work\\hp', 1.0)
            md = (Path(d) / 'maillages.md').read_text(encoding='utf-8')
            bat = (Path(d) / 'extraire_echantillons.bat').read_text(encoding='utf-8')
        self.assertIn('**Passent le chemin d\'export de DECA : 2 / 4 (50.0 %)**', md)
        self.assertIn('| Position | R16G16B16_SNORM | 4 | 4 |', md)
        self.assertIn('--noeuds 102 900', bat)
        self.assertIn('--noeuds 103', bat)


class TestAuditProjet(unittest.TestCase):
    def test_end_to_end_on_fake_project(self):
        """Faux projet (vrai scan) : un .meshc type ADF mais illisible est compte en echec sans arreter l'audit ; un
        fichier d'un autre type qui porte le meme nom (collision de hash) n'est pas pris ; les dossiers jc* sont
        ecartes. Puis --modeles sur un .modelc illisible."""
        import os
        import sqlite3
        import tempfile
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import faux_projet
        with tempfile.TemporaryDirectory() as d:
            project = faux_projet.build_fake_project(d, [('models/x/faux.meshc', b'pas un ADF' * 10),
                                                         ('models/x/texture_homonyme.bin', b'\x80\x45\x21\x70' * 64),
                                                         ('models/jc_vehicles/b.meshc', b'pas un ADF' * 10),
                                                         ('models/x/faux.modelc', b'pas un ADF' * 12),
                                                         ('docs/a.txt', b'texte\n' * 20)])
            db = sqlite3.connect(os.path.join(project, 'db', 'core.db'))
            db.execute("UPDATE core_nodes SET file_type = 'adf' WHERE CAST(v_path AS TEXT) LIKE '%.meshc' "
                       "OR CAST(v_path AS TEXT) LIKE '%.modelc'")
            # collision : un fichier sans type porte le meme nom que le .meshc, avec un numero de noeud plus petit
            uid_meshc = db.execute("SELECT node_id FROM core_nodes WHERE CAST(v_path AS TEXT) = 'models/x/faux.meshc'"
                                   ).fetchone()[0]
            db.execute("UPDATE core_nodes SET v_path = ?, file_type = NULL WHERE CAST(v_path AS TEXT) = "
                       "'models/x/texture_homonyme.bin'", (b'models/x/faux.meshc',))
            db.commit()
            db.close()
            out = Path(d) / 'sortie'
            self.assertEqual(AM.main([project, '--sortie', str(out), '--processus', '1']), 0)
            lignes = (out / 'maillages.csv').read_text(encoding='utf-8-sig').splitlines()
            md = (out / 'maillages.md').read_text(encoding='utf-8')
            bat = (out / 'extraire_echantillons.bat').read_text(encoding='utf-8')
            out2 = Path(d) / 'modeles'
            self.assertEqual(AM.main([project, '--modeles', '--sortie', str(out2), '--processus', '1']), 0)
            md2 = (out2 / 'modeles.md').read_text(encoding='utf-8')
            lignes2 = (out2 / 'modeles.csv').read_text(encoding='utf-8-sig').splitlines()
            out3 = Path(d) / 'materiaux'
            self.assertEqual(AM.main([project, '--materiaux', '--sortie', str(out3), '--processus', '1']), 0)
            md3 = (out3 / 'materiaux.md').read_text(encoding='utf-8')
        self.assertEqual(len(lignes), 2)
        self.assertTrue(lignes[1].startswith('models/x/faux.meshc;'))
        self.assertNotIn(';ok;', lignes[1])
        self.assertIn('0 / 1', md)
        self.assertIn('1 herites de Just Cause', md)
        self.assertIn('--noeuds {}'.format(uid_meshc), bat)          # le noeud ADF, pas l'homonyme
        self.assertEqual(len(lignes2), 2)
        self.assertTrue(lignes2[1].startswith('models/x/faux.modelc;echec_lecture;'))
        self.assertIn('Exportes sans defaut : 0 / 1', md2)
        self.assertIn('1 modeles lus ; 0 materiaux', md3)


if __name__ == '__main__':
    unittest.main()
