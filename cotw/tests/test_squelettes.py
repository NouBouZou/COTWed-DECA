#!/usr/bin/env python3
"""
Tests des squelettes : export glTF par le lecteur Havok du fork (deca.ff_adf_amf_gltf.Deca3dHkSkeleton, sans
bin2xml), couples (modele, squelette) des entites RTPC (deca.export_import_rtpc.rtpc_node_model_skeleton) et
mesures de cotw/audit_squelettes.py.

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_squelettes.py
"""
import io
import math
import os
import sqlite3
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import audit_squelettes as AS  # noqa: E402
import havok_fabrique as HF  # noqa: E402
from test_maillages import attr, maillage  # noqa: E402
from deca import ff_adf_amf_gltf as G  # noqa: E402

R2 = math.sqrt(0.5)
# Root a l'origine ; Spine a (1, 0, 0), tournee de 90 degres autour de z ; Tip a (1, 0, 0) dans Spine : (1, 1, 0)
POSES = [((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)),
         ((1.0, 0.0, 0.0), (0.0, 0.0, R2, R2), (1.0, 1.0, 1.0)),
         ((1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))]


def squelette_bsk():
    return HF.skeleton_and_animation(bones=('Root', 'Spine', 'Tip'), parents=(-1, 0, 1), poses=POSES)


class FauxVfs:
    """Juste ce que lisent Deca3dHkSkeleton et rtpc_node_model_skeleton."""

    class logger:
        @staticmethod
        def log(*_a, **_k):
            pass

    def __init__(self, fichiers):
        self.fichiers = fichiers      # v_path -> (file_type, octets)

    def nodes_where_match(self, v_path=None):
        if v_path not in self.fichiers:
            return []
        t, data = self.fichiers[v_path]
        return [types.SimpleNamespace(v_path=v_path, file_type=t, data=data, size_u=len(data))]

    def file_obj_from(self, node):
        return io.BytesIO(node.data)

    def hash_string_match(self, hash32=None):
        return []

    def adf_type_map_load(self):
        return {}, set()


class TestSkeletonMatrices(unittest.TestCase):
    def test_world_positions_and_rotation(self):
        bones = [{'name': n, 'parent': p, 'translation': list(t), 'rotation': list(r), 'scale': list(s)}
                 for n, p, (t, r, s) in zip(('Root', 'Spine', 'Tip'), (-1, 0, 1), POSES)]
        local, world = G.skeleton_matrices(bones)
        np.testing.assert_allclose([w[0:3, 3] for w in world], [[0, 0, 0], [1, 0, 0], [1, 1, 0]], atol=1e-6)
        # x de Spine pointe vers +y
        np.testing.assert_allclose(world[1][0:3, 0], [0, 1, 0], atol=1e-6)
        np.testing.assert_allclose(G.quaternion_matrix((0, 0, 0, 1)), np.eye(3))
        # os sans pose de reference : identite
        _l, w = G.skeleton_matrices([{'name': 'A', 'parent': -1}])
        np.testing.assert_allclose(w[0], np.eye(4))


class TestSkeletonExport(unittest.TestCase):
    def test_skin_from_native_havok_reader(self):
        import pygltflib as pyg
        vfs = FauxVfs({b'animations/test/rig.bsk': ('h2014', squelette_bsk())})
        with tempfile.TemporaryDirectory() as d:
            db = types.SimpleNamespace(resource_prefix_abs=d, resource_prefix_uri='res', flat_file_layout=False)
            gltf = pyg.GLTF2()
            scene = pyg.Scene()
            # le chemin cite par l'entite peut porter une autre extension : l'export prend le .bsk
            sk = G.Deca3dHkSkeleton(b'animations/test/rig.skeleton', scene)
            skin_idx, skin, bone_nodes = sk.add_to_gltf(vfs, None, db, gltf)
            ibm = np.frombuffer(Path(d, 'animations/test/rig.bsk.ibm.dat').read_bytes(), dtype=np.float32)
        self.assertEqual(skin_idx, 0)
        self.assertEqual([gltf.nodes[i].name for i in skin.joints], ['Root', 'Spine', 'Tip'])
        self.assertEqual(scene.nodes, [skin.joints[0]])                   # une seule racine dans la scene
        self.assertEqual(gltf.nodes[skin.joints[0]].children, [skin.joints[1]])
        self.assertEqual(gltf.nodes[skin.joints[1]].children, [skin.joints[2]])
        np.testing.assert_allclose(gltf.nodes[skin.joints[1]].rotation, [0, 0, R2, R2], atol=1e-6)
        self.assertEqual(gltf.accessors[skin.inverseBindMatrices].count, 3)
        # matrices inverses, colonne par colonne : l'inverse de Tip ramene (1, 1, 0) a l'origine
        m = ibm.reshape(3, 4, 4).transpose(0, 2, 1)
        np.testing.assert_allclose(m[2] @ np.array([1, 1, 0, 1]), [0, 0, 0, 1], atol=1e-6)
        self.assertEqual(sk.add_to_gltf(vfs, None, db, gltf)[0], 0)      # une seule fois par squelette

    def test_attachment_hangs_on_its_bone(self):
        """Piece rapportee (os d'attache) : noeud rigide, enfant de l'os, sous la matrice locale de l'entite ; os
        inconnu : exportee comme avant ; piece ordinaire : maillage avec la peau du squelette."""
        vfs = FauxVfs({b'animations/test/rig.bsk': ('h2014', squelette_bsk())})
        with tempfile.TemporaryDirectory() as d:
            g = G.DecaGltf(vfs, d, 'test/x', include_skeleton=True)
            g.db.gltf_add_modelc = lambda _g, v, material_properties=None: [[([7], True)]]
            local = G.Deca3dMatrix(col_major=[1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0.01, 1.0])
            with g.scene():
                with G.DecaGltfNode(g, name='entite') as racine:
                    g.export_modelc(b'models/test/bullet.modelc', None, skeleton_raw_path=b'animations/test/rig.skeleton',
                                    attach_bone=b'Tip', attach_matrix=local)
                    g.export_modelc(b'models/test/other.modelc', None, skeleton_raw_path=b'animations/test/rig.skeleton',
                                    attach_bone=b'Absent')
                    g.export_modelc(b'models/test/gun.modelc', None, skeleton_raw_path=b'animations/test/rig.skeleton')
        noeuds = g.gltf.nodes
        tip = next(n for n in noeuds if n.name == 'Tip')
        self.assertEqual(len(tip.children), 1)
        piece = noeuds[tip.children[0]]
        self.assertEqual(piece.name, 'bullet.modelc')
        np.testing.assert_allclose(piece.matrix[12:15], [0, 0, 0.01])
        maillage_piece = noeuds[piece.children[0]]
        self.assertEqual((maillage_piece.mesh, maillage_piece.skin), (7, None))     # rigide, pas de peau
        enfants = [noeuds[i] for i in racine.children]
        self.assertEqual([n.name for n in enfants], ['other.modelc', 'gun.modelc'])
        self.assertEqual(noeuds[enfants[1].children[0]].skin, 0)                    # peau du squelette
        self.assertEqual(len(g.gltf.skins), 1)

    def test_missing_and_unreadable_skeleton(self):
        import pygltflib as pyg
        from deca.errors import EDecaFileMissing, EDecaErrorParse
        vfs = FauxVfs({b'a/bad.bsk': ('h2014', b'\0' * 64)})
        db = types.SimpleNamespace(resource_prefix_abs='.', resource_prefix_uri='res', flat_file_layout=False)
        with self.assertRaises(EDecaFileMissing):
            G.Deca3dHkSkeleton(b'a/none.bsk', pyg.Scene()).add_to_gltf(vfs, None, db, pyg.GLTF2())
        with self.assertRaises(EDecaErrorParse):
            G.Deca3dHkSkeleton(b'a/bad.bsk', pyg.Scene()).add_to_gltf(vfs, None, db, pyg.GLTF2())


def maillage_a_os(points, os_locaux, table):
    """Maillage aux positions R32G32B32_FLOAT, un os par sommet (poids 255 sur l'indice local donne)."""
    n = len(points)
    stride = 12 + 4 + 4
    buf = bytearray()
    for p, o in zip(points, os_locaux):
        buf += struct.pack('<3f4B4B', *p, o, 0, 0, 0, 255, 0, 0, 0)
    m = maillage(n, [attr(b'AmfUsage_Position', b'AmfFormat_R32G32B32_FLOAT', 0, stride),
                     attr(b'AmfUsage_BoneIndex', b'AmfFormat_R8G8B8A8_UINT', 12, stride),
                     attr(b'AmfUsage_BoneWeight', b'AmfFormat_R8G8B8A8_UNORM', 16, stride)],
                 [stride], [0, 1, 2])
    m.boneIndexLookup = list(table)
    vb = types.SimpleNamespace(data=bytes(buf))
    return m, [vb]


class TestPeau(unittest.TestCase):
    def setUp(self):
        # chaine de 12 os le long de x, un tous les metres
        n = 12
        self.sq = AS.Squelette([{'name': 'b{}'.format(i), 'parent': i - 1,
                                 'translation': [0.0 if i == 0 else 1.0, 0.0, 0.0]} for i in range(n)])
        rng = np.random.default_rng(3)
        # sommets autour du milieu de chaque os (segment i -> i+1), ou de l'articulation pour le dernier
        self.points, self.os = [], []
        for i in range(n):
            for _k in range(20):
                x = i + (0.5 if i < n - 1 else 0.0) + rng.uniform(-0.3, 0.3)
                self.points.append((x, rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1)))
                self.os.append(i)

    def test_right_table(self):
        # table du maillage : os local k -> os n - 1 - k du squelette
        table = [11 - k for k in range(12)]
        m, vb = maillage_a_os(self.points, [11 - o for o in self.os], table)
        r = AS.mesurer_peau(m, vb, self.sq, np.random.default_rng(1))
        self.assertEqual((r['sommets'], r['hors'], r['table_max'], r['tires']), (240, 0, 11, 240))
        self.assertEqual(r['rang'], 240)
        self.assertLess(r['temoin'], 240 * 0.7)
        self.assertLess(float(np.median(r['distances'])), 0.02)

    def test_rigid_parts_and_bone_frame(self):
        table = list(range(12))
        m, vb = maillage_a_os(self.points, self.os, table)
        r = AS.mesurer_peau(m, vb, self.sq, np.random.default_rng(1))
        self.assertEqual(r['pivot'], 240)                       # chaque piece contient l'articulation de son os
        self.assertLess(r['pivot_temoin'], 240 * 0.5)
        self.assertEqual(sum(r['groupes'].values()), 240)
        self.assertEqual(len(r['groupes']), 12)
        self.assertLess(r['rang_local'], r['rang'])            # donnes dans le repere du modele, pas de l'os
        # les memes sommets donnes dans le repere de leur os (articulation i en (i, 0, 0), sans rotation)
        locaux = [(x - o, y, z) for (x, y, z), o in zip(self.points, self.os)]
        m, vb = maillage_a_os(locaux, self.os, table)
        r = AS.mesurer_peau(m, vb, self.sq, np.random.default_rng(1))
        self.assertEqual(r['rang_local'], 240)
        self.assertLess(r['rang'], 240 * 0.5)

    def test_placement(self):
        # sommets autour de l'origine, tous sur l'os 7 : a leur place une fois poses sur l'os 7
        locaux = [(x - i - (0.5 if i < 11 else 0.0), y, z) for (x, y, z), i in zip(self.points, self.os) if i == 7]
        m, vb = maillage_a_os(locaux, [7] * len(locaux), list(range(12)))
        brut = AS.mesurer_peau(m, vb, self.sq, np.random.default_rng(1))
        place = AS.mesurer_peau(m, vb, self.sq, np.random.default_rng(1),
                                AS.placement_attache(self.sq, b'b7', (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0,
                                                                      0.5, 0, 0, 1.0)))
        self.assertEqual(brut['pivot'], 0)
        self.assertEqual((place['rang'], place['pivot']), (20, 20))

    def test_details(self):
        import json
        m, vb = maillage_a_os(self.points, self.os, list(range(12)))
        d = AS.detail_maillage(m, vb, n_max=10)
        self.assertEqual((d['sommets'], d['table'], len(d['echantillon']['p'])), (240, list(range(12)), 10))
        self.assertEqual(d['echantillon']['os'][0], [0, 0, 0, 0])
        self.assertEqual(d['echantillon']['w'][0], [255, 0, 0, 0])
        r = AS.Resultat(b'models/a/b.modelc', b'animations/s.skeleton')
        with tempfile.TemporaryDirectory() as tmp:
            AS.ecrire_details(Path(tmp), r, self.sq, b'models/a/b.meshc', [d])
            j = json.loads(Path(tmp, 'models_a_b.modelc__s.json').read_text(encoding='utf-8'))
        self.assertEqual(len(j['os']), 12)
        self.assertEqual(j['os'][1]['monde'][0][3], 1.0)
        self.assertEqual(j['maillages'][0]['sommets'], 240)

    def test_wrong_table_is_seen(self):
        m, vb = maillage_a_os(self.points, self.os, [11 - k for k in range(12)])     # table a l'envers
        r = AS.mesurer_peau(m, vb, self.sq, np.random.default_rng(1))
        self.assertLess(r['rang'], 240 * 0.6)

    def test_bones_beyond_the_skeleton(self):
        m, vb = maillage_a_os(self.points, self.os, list(range(10)) + [40, 41])
        r = AS.mesurer_peau(m, vb, self.sq, np.random.default_rng(1))
        self.assertEqual(r['hors'], 40)
        self.assertEqual(r['tires'], 200)

    def test_mesh_without_bones(self):
        m = maillage(3, [attr(b'AmfUsage_Position', b'AmfFormat_R32G32B32_FLOAT', 0, 12)], [12], [0, 1, 2])
        self.assertIsNone(AS.mesurer_peau(m, [types.SimpleNamespace(data=b'\0' * 36)], self.sq,
                                          np.random.default_rng(1)))


def entite_rtpc():
    """CCharacter (modele et squelette) -> SCharacterPart (herite du squelette) ; CRigidObject sans squelette ;
    CRigidObject sans sa propriete de modele (ignore)."""
    import deca.ff_rtpc_write as W
    from deca.ff_rtpc import Rtpc, k_type_str, k_type_mat4x4
    part = W.make_node('part', [W.make_prop('_class', k_type_str, b'SCharacterPart'),
                                W.make_prop(0xb498c27d, k_type_str, b'models/test/part.modelc')])
    perso = W.make_node('perso', [W.make_prop('_class', k_type_str, b'CCharacter'),
                                  W.make_prop('skeleton', k_type_str, b'animations/test/rig.bsk'),
                                  W.make_prop(0xe8129fe6, k_type_str, b'models/test/body.modelc')], [part])
    rigide = W.make_node('rigide', [W.make_prop('_class', k_type_str, b'CRigidObject'),
                                    W.make_prop(0x32b409e0, k_type_str, b'models/test/rock.modelc')])
    casse = W.make_node('casse', [W.make_prop('_class', k_type_str, b'CRigidObject')])
    # arme : la cartouche pend a l'os Tip, decalee de 1 cm en z dans le repere de l'os
    cartouche = W.make_node('cartouche', [
        W.make_prop('_class', k_type_str, b'CModelAttachementWeaponComponent'),
        W.make_prop(0xdd3acc80, k_type_str, b'Tip'),
        W.make_prop('world', k_type_mat4x4, [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0.01, 1.0]),
        W.make_prop(0xf9dcf6ab, k_type_str, b'models/test/bullet.modelc')])
    arme = W.make_node('arme', [W.make_prop('_class', k_type_str, b'CBulletWeaponBase'),
                                W.make_prop('model_skeleton', k_type_str, b'animations/test/rig.skeleton'),
                                W.make_prop(0xf9dcf6ab, k_type_str, b'models/test/gun.modelc')], [cartouche])
    r = Rtpc()
    r.version = 1
    r.root_node = W.make_node(0, [], [perso, rigide, casse, arme])
    return W.rtpc_to_binary(r)


class TestCouples(unittest.TestCase):
    def test_entity_pairs(self):
        couples = AS.couples_rtpc(FauxVfs({}), entite_rtpc())
        local = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0.01, 1.0)
        self.assertEqual(sorted(couples), [
            (b'models/test/body.modelc', b'animations/test/rig.bsk', b'CCharacter', b'', ()),
            (b'models/test/bullet.modelc', b'animations/test/rig.skeleton', b'CModelAttachementWeaponComponent',
             b'Tip', local),
            (b'models/test/gun.modelc', b'animations/test/rig.skeleton', b'CBulletWeaponBase', b'', ()),
            (b'models/test/part.modelc', b'animations/test/rig.bsk', b'SCharacterPart', b'', ())])

    def test_attachment_placement(self):
        bones = [{'name': n, 'parent': p, 'translation': list(t), 'rotation': list(r), 'scale': list(s)}
                 for n, p, (t, r, s) in zip(('Root', 'Spine', 'Tip'), (-1, 0, 1), POSES)]
        sq = AS.Squelette(bones)
        m = AS.placement_attache(sq, b'Tip', (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0.5, 0, 0, 1.0))
        # Tip en (1, 1, 0), tourne de 90 degres : x local -> +y du modele
        np.testing.assert_allclose(m @ [0, 0, 0, 1], [1, 1.5, 0, 1], atol=1e-6)
        with self.assertRaises(AS.Statut):
            AS.placement_attache(sq, b'Absent', ())


class TestAuditProjet(unittest.TestCase):
    def test_end_to_end_on_fake_project(self):
        """Faux projet (vrai scan) : l'entite cite deux modeles illisibles et un squelette lisible ; une seconde
        entite cite un squelette absent. Les couples sont comptes, aucun ne fait tomber l'audit."""
        import faux_projet
        import deca.ff_rtpc_write as W
        from deca.ff_rtpc import Rtpc, k_type_str
        autre = W.make_node('x', [W.make_prop('_class', k_type_str, b'CCharacter'),
                                  W.make_prop('skeleton', k_type_str, b'animations/test/absent.bsk'),
                                  W.make_prop(0xe8129fe6, k_type_str, b'models/test/body.modelc')])
        r = Rtpc()
        r.version = 1
        r.root_node = W.make_node(0, [], [autre])
        with tempfile.TemporaryDirectory() as d:
            project = faux_projet.build_fake_project(d, [
                ('entities/test/perso.epe', entite_rtpc()),
                ('entities/test/autre.epe', W.rtpc_to_binary(r)),
                ('animations/test/rig.bsk', squelette_bsk()),
                ('models/test/body.modelc', b'pas un ADF' * 12),
                ('models/test/part.modelc', b'pas un ADF' * 13),
                ('models/jc_test/old.modelc', b'pas un ADF' * 14)])
            db = sqlite3.connect(os.path.join(project, 'db', 'core.db'))
            db.execute("UPDATE core_nodes SET file_type = 'adf' WHERE CAST(v_path AS TEXT) LIKE '%.modelc'")
            db.execute("UPDATE core_nodes SET file_type = 'rtpc' WHERE CAST(v_path AS TEXT) LIKE '%.epe'")
            db.execute("UPDATE core_nodes SET file_type = 'h2014' WHERE CAST(v_path AS TEXT) LIKE '%.bsk'")
            db.commit()
            db.close()
            out = Path(d) / 'sortie'
            self.assertEqual(AS.main([project, '--sortie', str(out), '--processus', '1']), 0)
            import csv
            with open(out / 'squelettes.csv', encoding='utf-8-sig') as f:
                lignes = {(l['modele'], l['squelette']): l for l in csv.DictReader(f, delimiter=';')}
            md = (out / 'squelettes.md').read_text(encoding='utf-8')
        self.assertEqual(len(lignes), 5)
        body = lignes[('models/test/body.modelc', 'animations/test/rig.bsk')]
        self.assertEqual((body['statut'], body['os'], body['classes']), ('modele_illisible', '3', 'CCharacter:1'))
        self.assertEqual(lignes[('models/test/part.modelc', 'animations/test/rig.bsk')]['statut'],
                         'modele_illisible')
        self.assertEqual(lignes[('models/test/body.modelc', 'animations/test/absent.bsk')]['statut'],
                         'squelette_absent')
        self.assertIn('Couples (modele, squelette, os d\'attache) distincts : 5** dont 1 pieces rapportees', md)
        cartouche = lignes[('models/test/bullet.modelc', 'animations/test/rig.skeleton')]
        self.assertEqual((cartouche['attache'], cartouche['statut']), ('Tip', 'modele_absent'))


if __name__ == '__main__':
    unittest.main()
