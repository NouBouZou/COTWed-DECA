#!/usr/bin/env python3
"""
Tests du decodage des animations Havok (deca/ff_havok_anim.py : hkaSplineCompressedAnimation) et de leur export glTF
(DecaGltf.export_animation).

Les blocs sont fabriques par havok_fabrique.encode_block d'apres la disposition relevee sur les fichiers du jeu
(2026-10-28 ; aucun fichier du jeu n'est versionne). Sur les vrais fichiers, la preuve est ailleurs : chaque bloc
finit a m_floatBlockOffsets et la derniere image d'un bloc egale la premiere du suivant (journal de recherche).

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_animations.py
"""
import io
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import havok_fabrique as HF  # noqa: E402
from deca import ff_havok as H  # noqa: E402
from deca import ff_havok_anim as A  # noqa: E402

R2 = math.sqrt(0.5)
QZ90 = (0.0, 0.0, R2, R2)


def norm(q):
    q = np.asarray(q, dtype=np.float64)
    return q / np.linalg.norm(q)


def same_rotation(a, b, tol):
    return abs(abs(float(np.dot(norm(a), norm(b)))) - 1.0) < tol


class TestSplines(unittest.TestCase):
    def test_linear_spline_hits_its_points(self):
        pts = np.array([[0.0, 0, 0], [1, 2, 3], [4, 4, 4], [9, 0, -1]])
        c = A.Curve('spline', degree=1, knots=[0, 0, 1, 2, 3, 3], points=pts)
        np.testing.assert_allclose(c.evaluate(4), pts)

    def test_cubic_clamped_spline(self):
        pts = np.array([[0.0], [1.0], [3.0], [4.0]])
        c = A.Curve('spline', degree=3, knots=[0, 0, 0, 0, 4, 4, 4, 4], points=pts)
        v = c.evaluate(5)[:, 0]
        self.assertAlmostEqual(v[0], 0.0)
        self.assertAlmostEqual(v[4], 4.0)
        # Bezier cubique en t = 1/2 : (P0 + 3 P1 + 3 P2 + P3) / 8
        self.assertAlmostEqual(v[2], (0 + 3 + 9 + 4) / 8)


class TestQuaternions(unittest.TestCase):
    def test_identity(self):
        np.testing.assert_allclose(A.quaternion_threecomp40(HF.quat_threecomp40((0, 0, 0, 1))), [0, 0, 0, 1], atol=1e-9)
        np.testing.assert_allclose(A.quaternion_threecomp48(HF.quat_threecomp48((0, 0, 0, 1))), [0, 0, 0, 1], atol=1e-9)

    def test_round_trip(self):
        rng = np.random.default_rng(4)
        for _ in range(200):
            q = norm(rng.normal(size=4))
            self.assertTrue(same_rotation(A.quaternion_threecomp40(HF.quat_threecomp40(q)), q, 2e-6))
            self.assertTrue(same_rotation(A.quaternion_threecomp48(HF.quat_threecomp48(q)), q, 2e-8))

    def test_unknown_quantization(self):
        with self.assertRaises(A.EHavokAnim):
            A.read_quaternion(b'\0' * 8, 0, 0)          # POLAR32 : pas encore vu dans COTW


def animation_bytes():
    """3 os, 7 images, blocs de 4 images (le bloc 1 commence a l'image 3, partagee).
    Root : position statique (x, z), rotation statique non compressee ; Spine : position et rotation sur des splines
    lineaires ; Tip : rotation statique THREECOMP48, sans position."""
    def bloc(debut):
        k = [0, 0, 1, 2, 3, 3]
        pos = [[float(debut + f), 0.5 * (debut + f), 0.0] for f in range(4)]
        quats = [norm((0, 0, math.sin((debut + f) * 0.1), math.cos((debut + f) * 0.1))) for f in range(4)]
        return [{'position': ('static', {0: 1.0, 2: 0.5}), 'rotation': ('static', QZ90, 5)},
                {'position': ('spline', 1, k, pos), 'rotation': ('spline', 1, k, quats, 5)},
                {'rotation': ('static', norm((0.1, 0.2, 0.3, 0.9)), 2)}]
    return HF.skeleton_and_animation(bones=('Root', 'Spine', 'Tip'), parents=(-1, 0, 1),
                                     spline={'frames': 7, 'per_block': 4, 'blocks': [bloc(0), bloc(3)]})


class TestDecode(unittest.TestCase):
    def setUp(self):
        self.pf = H.Packfile(animation_bytes())
        (self.o, self.binding), = A.animations_of(self.pf)
        self.d = A.decode(self.pf, self.o)

    def test_structure(self):
        d = self.d
        self.assertEqual(d['rotations'].shape, (7, 3, 4))
        self.assertEqual([b['frames'] for b in d['blocks']], [4, 4])
        self.assertTrue(all(b['end_ok'] for b in d['blocks']))
        self.assertEqual(dict(d['quantizations']), {(1, 5, 1): 4, (1, 2, 1): 2})
        self.assertEqual(d['track_names'], ['Root', 'Spine', 'Tip'])
        self.assertAlmostEqual(d['frame_duration'], 1 / 30, places=6)

    def test_values(self):
        d = self.d
        np.testing.assert_allclose(d['translations'][:, 0], [[1.0, 0.0, 0.5]] * 7, atol=1e-6)
        np.testing.assert_allclose(d['rotations'][:, 0], [QZ90] * 7, atol=1e-6)
        np.testing.assert_allclose(d['translations'][:, 1, 0], np.arange(7), atol=1e-3)   # 16 bits
        np.testing.assert_allclose(d['translations'][:, 1, 1], 0.5 * np.arange(7), atol=1e-3)
        for f in range(7):
            self.assertTrue(same_rotation(d['rotations'][f, 1], (0, 0, math.sin(f * 0.1), math.cos(f * 0.1)), 1e-9))
        self.assertTrue(same_rotation(d['rotations'][0, 2], (0.1, 0.2, 0.3, 0.9), 1e-7))
        np.testing.assert_allclose(d['translations'][:, 2], 0.0)
        np.testing.assert_allclose(d['scales'], 1.0)
        # image 3 partagee par les deux blocs
        self.assertLess(d['continuity']['rotation'], 1e-6)
        self.assertLess(d['continuity']['translation'], 1e-3)

    def test_bones_by_binding(self):
        self.assertEqual(A.track_bones(self.d, self.binding, ['Tip', 'Root', 'Spine']), [1, 2, 0])
        # sans liaison : noms des pistes d'annotation
        self.assertEqual(A.track_bones(self.d, None, ['Spine', 'Root']), [1, 0, None])

    def test_truncated_block_is_refused(self):
        data = animation_bytes()
        pf = H.Packfile(data)
        o, _b = A.animations_of(pf)[0]
        info = H.decode_animation(pf, o)
        dt, dn = pf.array(o.offset + 152)
        base = pf.data_section.start + dt
        with self.assertRaises(A.EHavokAnim):
            A.read_block(pf.data, base, base + 20, 3, 0, info['maskAndQuantizationSize'])


class FauxVfs:
    class logger:
        @staticmethod
        def log(*_a, **_k):
            pass

        @staticmethod
        def warning(*_a, **_k):
            pass

    def __init__(self, fichiers):
        self.fichiers = fichiers

    def nodes_where_match(self, v_path=None):
        if v_path not in self.fichiers:
            return []
        t, data = self.fichiers[v_path]
        return [types.SimpleNamespace(v_path=v_path, file_type=t, data=data, size_u=len(data))]

    def file_obj_from(self, node):
        return io.BytesIO(node.data)

    def adf_type_map_load(self):
        return {}, set()


class TestGltf(unittest.TestCase):
    def test_export_animation_on_the_skeleton(self):
        import pygltflib as pyg
        from deca import ff_adf_amf_gltf as G
        data = animation_bytes()
        vfs = FauxVfs({b'animations/test/rig.bsk': ('h2014', data), b'animations/test/walk.ban': ('h2014', data)})
        with tempfile.TemporaryDirectory() as d:
            g = G.DecaGltf(vfs, d, 'test/walk', include_skeleton=True)
            with g.scene():
                lignes = g.export_animation(b'animations/test/walk.ban', b'animations/test/rig.skeleton')
            g.gltf_save()
            gl = pyg.GLTF2().load(g.export_path)
            binaire = Path(d, 'animations/test/walk.ban.anim_00.bin').read_bytes()
        self.assertEqual(lignes, ['walk.ban : 7 frames, 3 tracks on bones, 0 without bone, normal'])
        (anim,) = gl.animations
        self.assertEqual(len(anim.channels), 9)
        noms = {gl.nodes[c.target.node].name for c in anim.channels}
        self.assertEqual(noms, {'Root', 'Spine', 'Tip'})
        temps = gl.accessors[anim.samplers[0].input]
        self.assertEqual((temps.count, temps.type), (7, 'SCALAR'))
        self.assertAlmostEqual(temps.max[0], 6 / 30, places=6)
        rot = next(c for c in anim.channels if c.target.path == 'rotation')
        self.assertEqual(gl.accessors[anim.samplers[rot.sampler].output].type, 'VEC4')
        self.assertEqual(len(binaire), gl.buffers[-1].byteLength)
        self.assertEqual(len(binaire), 7 * 4 + 3 * 7 * 4 * (3 + 4 + 3))



class TestAudit(unittest.TestCase):
    def test_audit_file(self):
        import audit_animations as AA
        (r,) = AA.auditer_fichier(animation_bytes(), 'animations/test/walk.ban')
        self.assertEqual((r.statut, r.images, r.pistes, r.blocs, r.fins_justes, r.liaison), ('ok', 7, 3, 2, '2/2',
                                                                                              'normal'))
        self.assertLess(r.ecart_rotation, 1e-6)
        self.assertLess(r.norme_quaternions, 1e-6)
        (bad,) = AA.auditer_fichier(b'pas un packfile' * 8, 'x.ban')
        self.assertEqual(bad.statut, 'packfile_illisible')
        (vide,) = AA.auditer_fichier(HF.navmesh_and_physics(), 'y.ban')
        self.assertEqual(vide.statut, 'sans_animation')
        with tempfile.TemporaryDirectory() as d:
            L = AA.ecrire(Path(d), [r, bad, vide], 'projet', 1.0, 3)
            lignes = Path(d, 'animations.csv').read_text(encoding='utf-8-sig').splitlines()
        self.assertIn('- **Animations a splines decodees sans defaut : 1 / 1** (100.0 %)', L)
        self.assertIn('| 16 bits | UNCOMPRESSED | 16 bits | 4 |', L)
        self.assertEqual(len(lignes), 4)


class TestProjet(unittest.TestCase):
    def test_audit_and_export_on_fake_project(self):
        """Faux projet (vrai scan) : un squelette et une animation ; audit puis export glTF (sans modele)."""
        import faux_projet
        import audit_animations as AA
        import exporter_animation as EA
        import pygltflib as pyg
        data = animation_bytes()
        with tempfile.TemporaryDirectory() as d:
            projet = faux_projet.build_fake_project(d, [('animations/skeletons/test/rig.bsk', data),
                                                        ('animations/test/walk.ban', data),
                                                        ('animations/test/run.ban', data)])
            # chemins en texte, comme dans un vrai projet (faux_projet les range en octets ; nodes_where_match
            # compare du texte)
            import os
            import sqlite3
            db = sqlite3.connect(os.path.join(projet, 'db', 'core.db'))
            db.execute('UPDATE core_nodes SET v_path = CAST(v_path AS TEXT) WHERE v_path IS NOT NULL')
            db.commit()
            db.close()
            out = Path(d) / 'audit'
            self.assertEqual(AA.main([projet, '--sortie', str(out), '--processus', '1']), 0)
            md = (out / 'animations.md').read_text(encoding='utf-8')
            exp = Path(d) / 'export'
            self.assertEqual(EA.main([projet, '--squelette', 'animations/skeletons/test/rig.skeleton',
                                      '--animations', 'animations/test/%', '--max', '5', '--sortie', str(exp)]), 0)
            (fichier,) = list(exp.glob('*.gltf'))
            gl = pyg.GLTF2().load(str(fichier))
        self.assertIn('decodees sans defaut : 2 / 2', md)
        self.assertEqual(len(gl.animations), 2)
        self.assertEqual(sorted(a.name for a in gl.animations), ['run.ban', 'walk.ban'])
        self.assertEqual(len(gl.skins), 1)


if __name__ == '__main__':
    unittest.main()
