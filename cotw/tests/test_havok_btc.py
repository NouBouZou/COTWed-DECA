#!/usr/bin/env python3
"""
Tests des packfiles Havok 2014 (deca.ff_havok : .ban, .bsk, .brd, .navmeshc, .physicsc) et des arbres de
comportement compiles .btc (deca.ff_btc), lus par le fork depuis le 2026-10-09 ; leur detection (ff_determine) ;
leurs vues (arbre Havok, texte de l'onglet Structure). Le routage de l'interface et retyper sont verifies sur le
faux projet de test_formats_l2.py, qui contient un .bsk et un .btc fabriques ici.

Fichiers fabriques par havok_fabrique.py avec la disposition des echantillons du jeu : aucun fichier du jeu n'est
versionne.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_havok_btc.py
"""
import io
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows)
import havok_fabrique as HF  # noqa: E402

import deca.ff_havok as H  # noqa: E402
from deca.ff_btc import Btc, is_btc  # noqa: E402
from deca.ff_determine import determine_file_type_and_size  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402


def determine(data: bytes):
    return determine_file_type_and_size(io.BytesIO(data), len(data), False)[0]


def h32(s):
    return int(hash32_func(s.encode())) & 0xFFFFFFFF


class TestPackfile(unittest.TestCase):
    def setUp(self):
        self.data = HF.skeleton_and_animation(bones=('Root', 'Spine', 'RightShoulder'), parents=(-1, 0, 1))
        self.pf = H.Packfile(self.data)

    def test_header_sections_objects(self):
        pf = self.pf
        self.assertEqual((pf.version, pf.file_version, pf.layout[:2]), ('hk_2014.1.0-r1', 11, (8, 1)))
        self.assertEqual([s.name for s in pf.sections], ['__classnames__', '__types__', '__data__'])
        self.assertEqual([o.class_name for o in pf.objects],
                         ['hkRootLevelContainer', 'hkaAnimationContainer', 'hkaSkeleton',
                          'hkaSplineCompressedAnimation', 'hkaAnimationBinding'])
        self.assertIs(pf.root(), pf.objects[0])
        self.assertTrue(H.is_packfile(self.data))
        self.assertEqual(H.summary_lines(pf)[0], 'Havok packfile hk_2014.1.0-r1 (file version 11), 3 sections, '
                                                 '5 objects')
        sizes = [o.size for o in pf.objects]
        self.assertEqual(sum(sizes), pf.data_section.local_fixups - pf.objects[0].offset)

    def test_root_and_container(self):
        pf = self.pf
        root = H.decode(pf, pf.root())
        v = root['namedVariants'][0]
        self.assertEqual((v['name'], v['className']), ('Animation Container', 'hkaAnimationContainer'))
        self.assertIs(v['variant'], pf.objects[1])
        cont = H.decode(pf, v['variant'])
        self.assertEqual([o.class_name for o in cont['skeletons']], ['hkaSkeleton'])
        self.assertEqual([o.class_name for o in cont['animations']], ['hkaSplineCompressedAnimation'])
        self.assertEqual([o.class_name for o in cont['bindings']], ['hkaAnimationBinding'])
        self.assertEqual(cont['skins'], [])

    def test_skeleton(self):
        sk = H.decode(self.pf, self.pf.objects[2])
        self.assertEqual(sk['name'], 'Reference')
        self.assertEqual([(b['name'], b['parent']) for b in sk['bones']],
                         [('Root', -1), ('Spine', 0), ('RightShoulder', 1)])
        self.assertEqual(sk['bones'][2]['translation'], [2.0, 0.5, 0.0])
        self.assertEqual(sk['bones'][1]['rotation'], [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(sk['bones'][0]['scale'], [1.0, 1.0, 1.0])
        self.assertEqual((sk['referenceFloats'], sk['floatSlots'], sk['partitions']), ([], [], []))

    def test_spline_animation(self):
        an = H.decode(self.pf, self.pf.objects[3])
        self.assertEqual(an['type'], '3 (spline compressed)')
        self.assertAlmostEqual(an['duration'], 1.5)
        self.assertEqual((an['numberOfTransformTracks'], an['numFrames'], an['numBlocks']), (3, 46, 1))
        self.assertAlmostEqual(an['frameDuration'], 1 / 30, places=6)
        self.assertEqual([t['name'] for t in an['annotationTracks']], ['Root', 'Spine', 'RightShoulder'])
        self.assertIsNone(an['extractedMotion'])

    def test_binding_hash16(self):
        """APEX : transformTrackToBoneIndices = hash32(nom de l'os) & 0xFFFF, resolu par les pistes d'annotation."""
        self.assertEqual(HF.bone_hash16('RightShoulder'), 0xee80)          # valeur relevee sur un .ban du jeu
        self.assertEqual(H.bone_hash16('RightArm'), 0xb6cf)
        bi = H.decode(self.pf, self.pf.objects[4])
        self.assertIs(bi['animation'], self.pf.objects[3])
        self.assertEqual(bi['transformTrackToBoneIndices'],
                         [HF.bone_hash16(n) for n in ('Root', 'Spine', 'RightShoulder')])
        self.assertEqual(bi['trackBones'], ['Root', 'Spine', 'RightShoulder'])
        self.assertEqual(bi['blendHint'], 'normal')
        self.assertIsNone(bi['originalSkeletonName'])

    def test_navmesh_and_generic(self):
        pf = H.Packfile(HF.navmesh_and_physics())
        self.assertEqual([o.class_name for o in pf.objects], ['hkRootLevelContainer', 'hkaiNavMesh', 'hkpPhysicsData'])
        root = H.decode(pf, pf.root())
        self.assertEqual([v['className'] for v in root['namedVariants']], ['hkaiNavMesh', 'hkpPhysicsData'])
        nm = H.decode(pf, pf.objects[1])
        self.assertEqual((nm['faces'], nm['edges']), ('1 elements', '3 elements'))
        self.assertEqual(nm['vertices'], '3 elements, bounding box -10.0 0.0 -7.0 to 20.0 2.5 5.0')
        self.assertEqual((nm['faceDataStriding'], nm['edgeDataStriding']), (1, 0))
        ph = H.decode(pf, pf.objects[2])
        self.assertEqual(ph['+0x10'], 'physics_test')
        self.assertTrue(ph['+0x18'].startswith('array: 4 elements'), ph['+0x18'])
        self.assertIs(ph['+0x28'], pf.objects[1])

    def test_rejects(self):
        with self.assertRaises(H.EHavok):
            H.Packfile(b'\0' * 64)
        bad = bytearray(self.data)
        bad[16] = 4                                                          # pointeurs 32 bits : non geres
        with self.assertRaises(H.EHavok):
            H.Packfile(bytes(bad))
        cut = self.data[:0x40 + 0x80]
        with self.assertRaises(Exception):
            H.Packfile(cut)

    def test_determine(self):
        self.assertEqual(determine(self.data), 'h2014')
        self.assertEqual(determine(HF.navmesh_and_physics()), 'h2014')

    def test_tree_view(self):
        from deca_gui.tree_items import havok_tree
        from deca_gui.viewer_havok import havok_text

        def kids(item):
            return item.children()
        top = kids(havok_tree(self.pf, label='rig.bsk: '))
        self.assertTrue(top[0].cols[0].startswith('rig.bsk: Havok packfile hk_2014.1.0-r1'), top[0].cols)
        self.assertIn('section __data__', [c.cols[0] for c in kids(top[0])])
        self.assertEqual([t.cols[0] for t in top[1:]], [o.class_name for o in self.pf.objects])
        skeleton = {c.cols[0]: c for c in kids(top[3])}
        bones = kids(skeleton['bones'])
        self.assertEqual(bones[2].cols[0], '[2] RightShoulder')
        self.assertTrue(bones[2].cols[2].startswith('parent Spine | t 2 0.5 0'), bones[2].cols[2])
        binding = {c.cols[0]: c for c in kids(top[5])}
        tracks = binding['transformTrackToBoneIndices']
        self.assertEqual(tracks.cols[2], 'APEX: hash16 of the bone name (3 resolved)')
        self.assertEqual(kids(tracks)[2].cols[2], '0xee80  RightShoulder')
        self.assertNotIn('trackBones', binding)
        text = havok_text(self.pf)
        self.assertIn('1 x hkaSkeleton', text)
        self.assertIn('hkaAnimationBinding', text.splitlines()[-1])


class TestBtc(unittest.TestCase):
    def test_structure(self):
        data = HF.btc_bytes()
        self.assertTrue(is_btc(len(data), data[:48]))
        b = Btc(data)
        self.assertEqual(b.name, 'intermediate/ai/bts/animals/test_flee.bts')
        self.assertEqual([(p['hash'], p['index'], p['value'], p['offset']) for p in b.params],
                         [(h32('fly_preset'), 0, 7, 0), (h32('flee_distance'), 1, 120, 8)])
        self.assertEqual(b.strings(), ['ROAMING FAILED PATH', 'ACT_CALL'])
        self.assertEqual(len(b.trailer), 0x30)
        lines = b.dump_lines({h32('fly_preset'): 'fly_preset'})
        self.assertEqual(lines[0], 'Behaviour tree (btc version 18): intermediate/ai/bts/animals/test_flee.bts')
        self.assertIn('fly_preset', lines[3])
        self.assertIn('value 7', lines[3])
        self.assertIn(' ? ', lines[4])
        self.assertIn('  ROAMING FAILED PATH', lines)

    def test_without_params(self):
        data = HF.btc_bytes(params=())
        b = Btc(data)
        self.assertEqual(b.params, [])
        self.assertEqual(b.dump_lines()[2], 'parameters: 0')

    def test_rejects(self):
        data = HF.btc_bytes()
        for bad in (data + b'\0', data[:-1], data[:0x2f]):
            self.assertFalse(is_btc(len(bad), bad[:48]))
        wrong = bytearray(data)
        wrong[0] = 0x11
        self.assertFalse(is_btc(len(wrong), bytes(wrong[:48])))
        with self.assertRaises(ValueError):
            Btc(data + b'\0')

    def test_determine(self):
        self.assertEqual(determine(HF.btc_bytes()), 'btc')
        self.assertEqual(determine(HF.btc_bytes(params=())), 'btc')

    def test_structure_view_resolves_names(self):
        from deca_gui.viewer_structure import structure_lines

        class Noms:
            asked = []

            def prefetch(self, hashes):
                self.asked += list(hashes)

            def get(self, h):
                return {h32('flee_distance'): 'flee_distance'}.get(h)
        noms = Noms()
        lines = structure_lines('btc', HF.btc_bytes(), noms)
        self.assertEqual(noms.asked, [h32('fly_preset'), h32('flee_distance')])
        self.assertIn('flee_distance', lines[4])
        self.assertIn(' ? ', lines[3])
        self.assertEqual(structure_lines('btc', HF.btc_bytes())[1].split(';')[0], 'code: {} bytes (bytecode, not '
                         'decoded)'.format(len(Btc(HF.btc_bytes()).code)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
