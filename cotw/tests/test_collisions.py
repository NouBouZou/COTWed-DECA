#!/usr/bin/env python3
"""
Tests des formes de collision Havok Physics 2014 (deca/ff_havok_physics.py), de leur audit (cotw/audit_collisions.py)
et de leur export OBJ (cotw/exporter_collisions.py).

Les fichiers sont fabriques par havok_fabrique.physique d'apres la disposition relevee sur 15 fichiers du jeu
(2026-10-29 ; aucun fichier du jeu n'est versionne). Sur les vrais fichiers, la preuve est ailleurs : faces sur leurs
plans, sommets dans tous les plans, feuilles des formes composees dans leur boite englobante (journal de recherche).

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_collisions.py
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import havok_fabrique as HF  # noqa: E402
from deca import ff_havok as H  # noqa: E402
from deca import ff_havok_physics as F  # noqa: E402
from deca import ff_pfx as P  # noqa: E402


def lecteur(data=None):
    pf = H.Packfile(P.packfile_bytes(data or HF.physique()))
    return pf, F.Reader(pf)


class TestFormes(unittest.TestCase):
    def test_body_and_compound(self):
        pf, r = lecteur()
        (b,) = r.bodies()
        self.assertEqual(b.name, 'bloc_test_COL')
        self.assertEqual(list(b.position), [10.0, 0.0, 5.0])
        self.assertEqual(b.shape.kind, 'compound')
        self.assertEqual([c.kind for _m, _s, c in b.shape.instances], ['polytope', 'capsule', 'polytope'])
        self.assertIs(b.shape.instances[0][2], b.shape.instances[2][2])        # meme forme partagee
        m = b.shape.instances[2][0]
        self.assertTrue(np.allclose(m[:3, 0], [0, 1, 0]) and np.allclose(m[:3, 3], [3, 0, 0]))   # colonnes

    def test_polytope(self):
        _pf, r = lecteur()
        box = r.bodies()[0].shape.instances[0][2]
        self.assertEqual((len(box.vertices), len(box.faces), len(box.planes), len(box.indices)), (8, 6, 6, 24))
        self.assertAlmostEqual(box.convex_radius, 0.01, places=6)
        self.assertEqual(F.polytope_errors(box), {'on_plane': 0.0, 'outside': 0.0, 'normal': 0.0, 'range': 0.0})
        self.assertTrue(np.allclose(box.vertices.min(0), [-0.5, -1, -0.5]))
        self.assertTrue(F.polytope_closed(box))
        melange = F.Shape(box.class_name, box.offset)                       # indices hors de l'ordre du contour
        melange.faces, melange.indices = box.faces, box.indices.copy()
        melange.indices[[0, 1]] = melange.indices[[1, 0]]
        self.assertFalse(F.polytope_closed(melange))

    def test_checks_catch_a_wrong_layout(self):
        """Un decalage dans la lecture casse les controles : plans pris pour des sommets, boite trop petite."""
        pf, r = lecteur()
        box = r.bodies()[0].shape.instances[0][2]
        wrong = F.Shape(box.class_name, box.offset)
        wrong.kind, wrong.vertices, wrong.planes = 'polytope', box.planes[:, :3].copy() * 3, box.planes
        wrong.faces, wrong.indices = box.faces, box.indices
        self.assertGreater(F.polytope_errors(wrong)['outside'], 0.5)
        comp = r.bodies()[0].shape
        self.assertEqual(F.compound_aabb_excess(pf, comp), 0.0)
        for i in range(3):                                   # transformations transposees : la boite tournee sort
            m = comp.instances[i][0]
            m[:3, :3] = m[:3, :3].T
        self.assertGreater(F.compound_aabb_excess(pf, comp), 0.5)

    def test_capsule_and_sphere(self):
        pf, r = lecteur()
        caps = r.bodies()[0].shape.instances[1][2]
        self.assertEqual(caps.kind, 'capsule')
        self.assertEqual((list(caps.a), list(caps.b), caps.convex_radius), ([0, 0, 0], [0, 1, 0], 0.25))
        v, t = F.shape_mesh(caps)
        d = np.array([np.linalg.norm(p - [0, min(max(p[1], 0), 1), 0]) for p in v])   # distance au segment
        self.assertTrue(np.allclose(d, 0.25, atol=1e-9))
        self.assertGreater(len(t), 0)
        sphere = r.shape(next(o.offset for o in pf.objects if o.class_name == 'hknpSphereShape'))
        v, t = F.shape_mesh(sphere)
        self.assertTrue(np.allclose(np.linalg.norm(v, axis=1), 0.5))

    def test_meshes_placed_and_closed(self):
        meshes = F.scene_meshes(HF.physique())
        self.assertEqual([n for n, _v, _t in meshes], ['bloc_test_COL/0', 'bloc_test_COL/1', 'bloc_test_COL/2'])
        _n, v, t = meshes[2]                                 # boite tournee, en (3, 0, 0), puis corps : quart de tour
        self.assertTrue(np.allclose(v.min(0), [9.5, -0.5, 1.0]) and np.allclose(v.max(0), [10.5, 0.5, 3.0]))
        self.assertEqual(len(t), 12)
        _n, v, _t = meshes[1]                                # capsule couchee le long de +z, puis z -> x par le corps
        self.assertTrue(np.allclose(v.min(0), [9.75, 2.75, 4.75]) and np.allclose(v.max(0), [11.25, 3.25, 5.25]))
        for _n, v, t in meshes:                              # maillages fermes, faces tournees vers l'exterieur
            edges = {}
            for a, b, c in t:
                for e in ((a, b), (b, c), (c, a)):
                    edges[e] = edges.get(e, 0) + 1
            self.assertTrue(all(edges.get((b, a), 0) == 1 for (a, b) in edges), _n)
            centre = v.mean(axis=0)
            vol = sum(np.dot(np.cross(v[b] - v[a], v[c] - v[a]), v[a] - centre) for a, b, c in t)
            self.assertGreater(vol, 0)

    def test_packfile_without_pfx_and_hull(self):
        self.assertEqual(len(F.scene_meshes(HF.physique(pfx=False))), 3)
        s = F.Shape('hknpConvexShape', 0)
        s.kind, s.vertices = 'convex', np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0.2, 0.2, 0.2]], float)
        v, t = F.shape_mesh(s)
        self.assertEqual(len(t), 4)                          # tetraedre : le point interieur ne compte pas

    def test_havok_tab(self):
        pf, _r = lecteur()
        lines = H.summary_lines(pf)
        self.assertIn('collision: 1 bodies, 3 shapes (1 compound, 1 polytope, 1 capsule)', lines)
        box = next(o for o in pf.objects if o.class_name == 'hknpConvexPolytopeShape')
        d = H.decode(pf, box)
        self.assertEqual(d['kind'], 'polytope')
        self.assertEqual(len(d['faces']), 6)
        self.assertTrue(d['check'].startswith('face vertices within 0 of their plane'), d['check'])
        sysd = H.decode(pf, next(o for o in pf.objects if o.class_name == 'hknpPhysicsSystemData'))
        self.assertEqual(sysd['bodyCinfos'][0]['name'], 'bloc_test_COL')
        comp = H.decode(pf, next(o for o in pf.objects if o.class_name == 'hknpStaticCompoundShape'))
        self.assertEqual(comp['instances'][2], 'translation 3 0 0 ; scale 1 1 1 ; hknpConvexPolytopeShape ; rotated')


class TestOutils(unittest.TestCase):
    def test_audit(self):
        import audit_collisions as AC
        r = AC.auditer_octets(HF.physique(), 'x.pfxc')
        self.assertEqual(r.statut, 'ok', r.detail)
        self.assertEqual((r.corps, r.formes, r.polytopes, r.capsules, r.spheres, r.composees, r.instances),
                         (1, 4, 1, 1, 1, 1, 3))
        self.assertEqual(r.hors_boite, 0.0)
        abime = bytearray(HF.physique())
        pos = abime.index(struct.pack('<4f', 4.01, 3.25, 1.25, 0.0))         # boite englobante trop petite
        struct.pack_into('<f', abime, pos, 3.0)
        self.assertEqual(AC.auditer_octets(bytes(abime), 'x.pfxc').statut, 'hors_boite')
        self.assertEqual(AC.auditer_octets(b'PFX\0' + bytes(100), 'y.pfs').statut, 'illisible')
        with tempfile.TemporaryDirectory() as d:
            L = AC.ecrire(Path(d), [r], 'test', 0.0)
        self.assertIn('- **Decodes sans defaut : 1 / 1**', L)

    def test_export_obj(self):
        import exporter_collisions as EC
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'bloc.pfxc'
            src.write_bytes(HF.physique())
            self.assertEqual(EC.main(['--local', str(src), '--sortie', str(Path(d) / 'obj')]), 0)
            lignes = (Path(d) / 'obj' / 'bloc.pfxc.obj').read_text(encoding='utf-8').splitlines()
        self.assertEqual([l for l in lignes if l.startswith('o ')],
                         ['o 0000_bloc_test_COL_0', 'o 0001_bloc_test_COL_1', 'o 0002_bloc_test_COL_2'])
        self.assertEqual(sum(1 for l in lignes if l.startswith('f ')), 12 + 256 + 12)

    def test_export_with_models_on_fake_project(self):
        """--modeles : le .modelc du meme nom que le .pfxc, ou ce qui l'empeche (absent, illisible)."""
        import exporter_collisions as EC
        import faux_projet
        import io
        import os
        import sqlite3
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            projet = faux_projet.build_fake_project(d, [('models/test/bloc.pfxc', HF.physique()),
                                                        ('models/test/bloc.modelc', b'pas un ADF' * 12),
                                                        ('models/test/seul.pfxc', HF.physique())])
            db = sqlite3.connect(os.path.join(projet, 'db', 'core.db'))
            db.execute('UPDATE core_nodes SET v_path = CAST(v_path AS TEXT) WHERE v_path IS NOT NULL')
            db.commit()
            db.close()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(EC.main([projet, '--fichiers', 'models/test/%.pfxc', '--modeles',
                                          '--sortie', str(Path(d) / 'obj')]), 0)
            objs = sorted(p.name for p in (Path(d) / 'obj').glob('*.obj'))
        texte = out.getvalue()
        self.assertEqual(objs, ['models__test__bloc.pfxc.obj', 'models__test__seul.pfxc.obj'])
        self.assertIn('models/test/bloc.modelc : modele en ECHEC', texte)
        self.assertIn('models/test/seul.modelc : modele absent', texte)

    def test_audit_on_fake_project(self):
        """Faux projet (vrai scan) : un .pfxc et un .pfs trouves par le type pfx, audites."""
        import audit_collisions as AC
        import faux_projet
        import os
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            projet = faux_projet.build_fake_project(d, [('models/test/bloc.pfxc', HF.physique()),
                                                        ('worlds/test/lieu.pfs', HF.physique())])
            db = sqlite3.connect(os.path.join(projet, 'db', 'core.db'))
            db.execute('UPDATE core_nodes SET v_path = CAST(v_path AS TEXT) WHERE v_path IS NOT NULL')
            db.commit()
            db.close()
            out = Path(d) / 'audit'
            self.assertEqual(AC.main([projet, '--sortie', str(out), '--processus', '1']), 0)
            md = (out / 'collisions.md').read_text(encoding='utf-8')
        self.assertIn('- **Decodes sans defaut : 2 / 2**', md)
        self.assertIn('.pfs 1', md)


if __name__ == '__main__':
    unittest.main()
