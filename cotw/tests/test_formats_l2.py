#!/usr/bin/env python3
"""
Tests des formats de COTW lus par le fork depuis le 2026-10-09 (lot L2) : RTPC en ligne (world.bin, .smodc),
OBC version 1, .fmod_guids, police TrueType (.ttfc), tableaux bruts .rawc ; leur detection (ff_determine) ;
retyper.py sur un faux projet (vrai scan) ; le routage de l'interface vers leurs visionneuses. Le faux projet
contient aussi un squelette Havok (.bsk) et un arbre de comportement (.btc) : leurs lecteurs sont testes dans
test_havok_btc.py, leur routage (onglets Havok et Structure) et leur retypage ici.

Les fichiers sont fabriques ici avec les dispositions etablies sur les echantillons du jeu (journal de recherche,
2026-10-09) : aucun fichier du jeu n'est versionne.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_formats_l2.py [--spawn]
"""
import io
import math
import multiprocessing
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows ; niveau module pour "spawn")
import retyper as RT  # noqa: E402
import havok_fabrique as HF  # noqa: E402

import numpy as np  # noqa: E402

import deca.ff_rtpc_inline as I  # noqa: E402
import deca.ff_fmod_guids as G  # noqa: E402
import deca.ff_ttf as TTF  # noqa: E402
import deca.ff_rawc as RAW  # noqa: E402
from deca.ff_obc import Obc, obc_version  # noqa: E402
from deca.ff_determine import determine_file_type_and_size  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402

APP = None


def h32(s):
    return int(hash32_func(s.encode())) & 0xFFFFFFFF


# ---------------------------------------------------------------------------------------------------------
# Fichiers fabriques
# ---------------------------------------------------------------------------------------------------------

def smodc_bytes() -> bytes:
    """Comme sound/aesir/music/base_music.smodc, plus un noeud aux deux sections et tous les types vus."""
    leaf = I.InlineNode(h32('leaf'))
    props = [I.InlineProperty(h32('name'), I.T_STR, b'base_music'),
             I.InlineProperty(h32('type'), I.T_STR, b'base'),
             I.InlineProperty(h32('max_controllers'), I.T_U32, 256),
             I.InlineProperty(h32('far_load_distance'), I.T_F32, 2.5),
             I.InlineProperty(h32('world_pos'), I.T_VEC3, [1.0, 2.0, 3.0]),
             I.InlineProperty(h32('transform'), I.T_TRANSFORM, [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 10.0, 20.0, 30.0]),
             I.InlineProperty(h32('on_load_event'), I.T_EVENT, [0x4689f394, 0xFFFF000012345678])]
    module = I.InlineNode(h32('sound_module'), [(I.KIND_CHILDREN, [leaf]), (I.KIND_PROPERTIES, props)])
    return I.inline_to_bytes(I.InlineNode(None, [(I.KIND_CHILDREN, [module])]))


def obc_v1_bytes() -> bytes:
    rows = [[0.035, 2.135, 1.532, -1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 8196.4, 1034.3, 8196.5, 1],
            [0.5, 1040.1, 7786.8, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -472.6, 1950.4, 7908.3, 1]]
    return struct.pack('<II', 1, len(rows)) + b''.join(struct.pack('<19f', *r) for r in rows)


GUIDS = [uuid.UUID('e4be7f4e-866d-4c89-974c-b1ab00c66efe'), uuid.UUID('288a32f5-0f2f-43b9-a1ef-bc9add92723e')]


def fmod_guids_bytes(guids=GUIDS) -> bytes:
    return G.to_bytes(list(guids))


def ttf_bytes(family='Test Sans') -> bytes:
    text = family.encode('utf-16-be')
    name = struct.pack('>HHH', 0, 1, 18) + struct.pack('>HHHHHH', 3, 1, 0x409, 1, len(text), 0) + text
    head = struct.pack('>4sHHHH', b'\x00\x01\x00\x00', 1, 16, 0, 0)
    return head + struct.pack('>4sIII', b'name', 0, 28, len(name)) + name


def heightfield_bytes(n=64) -> bytes:
    y, x = np.mgrid[0:n, 0:n]
    return (100.0 + 50.0 * np.sin(x / 9.0) * np.cos(y / 7.0)).astype('<f4').tobytes()


def mask_bytes(n=128) -> bytes:
    y, x = np.mgrid[0:n, 0:n]
    return np.packbits(((x - n / 2) ** 2 + (y - n / 2) ** 2 < (n / 3) ** 2).astype(np.uint8)).tobytes()


def image_u8_bytes(n=128) -> bytes:
    y, x = np.mgrid[0:n, 0:n]
    return (40 + 20 * np.sin(x / 11.0) + 10 * np.cos(y / 5.0)).astype(np.uint8).tobytes()


def determine(data: bytes, check_text=True):
    return determine_file_type_and_size(io.BytesIO(data), len(data), check_text)[0]


# ---------------------------------------------------------------------------------------------------------
# Lecteurs
# ---------------------------------------------------------------------------------------------------------

class TestInlineRtpc(unittest.TestCase):
    def test_round_trip_and_structure(self):
        data = smodc_bytes()
        root = I.inline_from_bytes(data)
        self.assertEqual(I.inline_to_bytes(root), data)
        module = root.children[0]
        self.assertEqual(module.name_hash, h32('sound_module'))
        self.assertEqual([k for k, _items in module.sections], [I.KIND_CHILDREN, I.KIND_PROPERTIES])
        values = {p.name_hash: p.value for p in module.properties}
        self.assertEqual(values[h32('name')], b'base_music')
        self.assertEqual(values[h32('on_load_event')], [0x4689f394, 0xFFFF000012345678])
        self.assertTrue(I.is_inline_rtpc(data))

    def test_base_music_layout(self):
        """Octets exacts de base_music.smodc (51 octets) : un noeud vide, puis deux proprietes."""
        data = bytes.fromhex('01040001 0024a90c 97020400 010042f8 e1000005 000200'.replace(' ', '')) + \
            bytes.fromhex('84b61ad3030a00') + b'base_music' + bytes.fromhex('d688642f030400') + b'base'
        self.assertEqual(len(data), 51)
        root = I.inline_from_bytes(data)
        module = root.children[0]
        self.assertEqual(module.name_hash, 0x970ca924)
        self.assertEqual(module.children[0].sections, [])
        self.assertEqual([p.value for p in module.properties], [b'base_music', b'base'])
        self.assertEqual(I.inline_to_bytes(root), data)

    def test_rejects(self):
        data = smodc_bytes()
        for bad in (data + b'\0', data[:-1], b'\x01\x04\x00\x01\x00', b'\x01\x07\x00\x00\x00', b''):
            self.assertFalse(I.is_inline_rtpc(bad), bad[:12])
        with self.assertRaises(I.EInlineRtpc):
            I.inline_from_bytes(data + b'\0')

    def test_to_rtpc_for_the_viewer(self):
        from deca.ff_rtpc import k_type_mat4x4, k_type_str
        rtpc = I.to_rtpc(I.inline_from_bytes(smodc_bytes()))
        module = rtpc.root_node.child_table[0]
        self.assertEqual(len(module.child_table), 1)
        props = module.prop_map
        self.assertEqual(props[h32('name')].type, k_type_str)
        t = props[h32('transform')]
        self.assertEqual(t.type, k_type_mat4x4)
        self.assertEqual(t.data[12:16], [10.0, 20.0, 30.0, 1.0])      # translation, derniere ligne
        self.assertEqual(rtpc.version, 'inline')


class TestSmallFormats(unittest.TestCase):
    def test_obc_v1(self):
        data = obc_v1_bytes()
        self.assertEqual(obc_version(data), 1)
        self.assertIsNone(obc_version(data + b'\0'))
        o = Obc()
        o.deserialize(io.BytesIO(data))
        boxes = o.boxes()
        self.assertAlmostEqual(boxes[0]['half_extents'][0], 0.035, places=5)
        self.assertAlmostEqual(boxes[0]['position'][0], 8196.4, places=2)
        self.assertEqual(boxes[0]['w'], [0.0, 0.0, 0.0, 1.0])
        lines = o.dump_lines()
        self.assertTrue(lines[0].startswith('OBC version 1: 2 occluder boxes'))
        self.assertIn('pos   8196.400', lines[1])

    def test_obc_v4_still_read(self):
        data = struct.pack('<II', 4, 1) + struct.pack('<20f', *range(20))
        self.assertEqual(obc_version(data), 4)
        o = Obc()
        o.deserialize(io.BytesIO(data))
        self.assertIn('meaning not established', o.dump_lines()[0])

    def test_fmod_guids(self):
        data = fmod_guids_bytes()
        self.assertEqual(len(data), 8 + 32)
        self.assertEqual(G.parse(data)[2], GUIDS)
        self.assertEqual(data[8:12], bytes.fromhex('4e7fbee4'))      # GUID au format Windows (u32 petit-boutiste)
        self.assertTrue(G.is_fmod_guids(len(data), data[:8]))
        self.assertTrue(G.is_fmod_guids(8, fmod_guids_bytes([])))
        self.assertFalse(G.is_fmod_guids(len(data) + 16, data[:8]))
        self.assertIn('{e4be7f4e-866d-4c89-974c-b1ab00c66efe}', G.dump_lines(data)[1])
        self.assertEqual(G.dump_lines(fmod_guids_bytes([]))[1], '(empty list: no FMOD event)')

    def test_ttf(self):
        data = ttf_bytes()
        self.assertEqual(TTF.names(data)['family'], 'Test Sans')
        self.assertTrue(TTF.is_ttf_header(len(data), data[:256]))
        self.assertFalse(TTF.is_ttf_header(len(data) - 1, data[:256]))      # table au-dela du fichier
        self.assertIn('  family           Test Sans', TTF.dump_lines(data))

    def test_rawc_layouts(self):
        hf = RAW.guess_layout(heightfield_bytes(), 'global_heightfield.rawc')
        self.assertEqual((hf.kind, hf.width), ('f32', 64))
        mask = RAW.guess_layout(mask_bytes(), 'nv_water_cull_mask.rawc')
        self.assertEqual((mask.kind, mask.width), ('bits', 128))
        img = RAW.guess_layout(image_u8_bytes(), 'water_nvwaveworks_mod.rawc')
        self.assertEqual((img.kind, img.width), ('u8', 128))
        rgba = RAW.to_rgba8(hf)
        self.assertEqual(rgba.shape, (64, 64, 4))
        self.assertEqual(int(rgba[..., 0].max()), 255)
        self.assertIsNone(RAW.guess_layout(b'\0' * 1000))

    def test_u8_image_not_taken_for_floats(self):
        """Octets repetes (33 33 33 33) : lisses en f32, mais hors de toute plage de flottants plausible."""
        n = 64
        y, x = np.mgrid[0:n, 0:n]
        img = (np.where(x < n // 2, 0x33, 0x1f) + (y // 32)).astype(np.uint8).tobytes()
        self.assertEqual(RAW.guess_layout(img).kind, 'u8')

    def test_determine(self):
        self.assertEqual(determine(smodc_bytes()), 'rtpci')
        self.assertEqual(determine(obc_v1_bytes()), 'obc')
        self.assertEqual(determine(fmod_guids_bytes()), 'fmodguids')
        self.assertEqual(determine(fmod_guids_bytes([])), 'fmodguids')
        self.assertEqual(determine(ttf_bytes()), 'ttf')
        self.assertIsNone(determine(heightfield_bytes(), check_text=False))      # .rawc : par le nom seulement
        self.assertIsNone(determine(b'\x01\x04\x00' + b'\xff' * 40, check_text=False))
        self.assertEqual(determine(b'hello world, plain text file\n'), 'txt')
        self.assertIsNone(determine(b'hello world, plain text file\n', check_text=False))


# ---------------------------------------------------------------------------------------------------------
# Faux projet : retyper, puis l'interface
# ---------------------------------------------------------------------------------------------------------

FILES = [
    ('sound/sound_module_framework/test_root.smodc', smodc_bytes()),
    ('worlds/world2/locations/hp_trophyworld/lodges/lodge_test.obc', obc_v1_bytes()),
    ('editor/entities/test/weapon_test.fmod_guids', fmod_guids_bytes()),
    ('global/empty_test.fmod_guids', fmod_guids_bytes([])),
    ('textures/qui/test_font.ttfc', ttf_bytes()),
    ('worlds/world4/terrain/global_heightfield.rawc', heightfield_bytes()),
    ('animations/test/rig_test.bsk', HF.skeleton_and_animation()),
    ('ai/bts/animals/test_flee.btc', HF.btc_bytes()),
    ('settings/opaque_test.bin', b'\xfa\xce' * 64),
]
EXPECTED = {'sound/sound_module_framework/test_root.smodc': 'rtpci',
            'worlds/world2/locations/hp_trophyworld/lodges/lodge_test.obc': 'obc',
            'editor/entities/test/weapon_test.fmod_guids': 'fmodguids',
            'global/empty_test.fmod_guids': 'fmodguids',
            'textures/qui/test_font.ttfc': 'ttf',
            'worlds/world4/terrain/global_heightfield.rawc': 'rawc',
            'animations/test/rig_test.bsk': 'h2014',
            'ai/bts/animals/test_flee.btc': 'btc'}


def text_paths(project):
    """faux_projet ecrit v_path en BLOB ; DECA (nodes_where_match) le compare en texte, comme dans un vrai projet."""
    db = sqlite3.connect(os.path.join(project, 'db', 'core.db'))
    db.execute("UPDATE core_nodes SET v_path = CAST(v_path AS TEXT) WHERE typeof(v_path) = 'blob'")
    db.commit()
    db.close()


def types_of(project):
    db = sqlite3.connect(os.path.join(project, 'db', 'core.db'))
    rows = db.execute('SELECT v_path, file_type FROM core_nodes WHERE v_path IS NOT NULL').fetchall()
    db.close()
    return {_projet.to_text(v): _projet.to_text(t) for v, t in rows}


class TestRetyper(unittest.TestCase):
    """Le scan du fork type ces fichiers (.rawc par son nom : global_heightfield.rawc est dans les dictionnaires de
    DECA) ; un projet scanne avant le fork les a sans type : on le simule en effacant leurs types, puis retyper les
    retrouve et annuler les efface."""

    @classmethod
    def setUpClass(cls):
        import faux_projet
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_retyper_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, FILES)
        text_paths(cls.project)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_retype_then_undo(self):
        after_scan = types_of(self.project)
        for path, t in EXPECTED.items():
            self.assertEqual(after_scan[path], t, path)              # la detection du scan
        self.assertIsNone(after_scan['settings/opaque_test.bin'])
        db = sqlite3.connect(os.path.join(self.project, 'db', 'core.db'))
        db.execute('UPDATE core_nodes SET file_type = NULL WHERE v_path IN ({})'.format(','.join('?' * len(EXPECTED))),
                   list(EXPECTED))
        db.commit()
        db.close()

        report = Path(self.tmp.name) / 'retyper.md'
        RT.main(['ajouter', self.project, '--rapport', str(report)])
        after = types_of(self.project)
        for path, t in EXPECTED.items():
            self.assertEqual(after[path], t, path)
        self.assertIsNone(after['settings/opaque_test.bin'])
        text = report.read_text(encoding='utf-8')
        self.assertIn('| **Fichiers types** | {} |'.format(len(EXPECTED)), text)
        self.assertIn('| `.fmod_guids` | `fmodguids` | 2 |', text)
        self.assertIn('| `.bin` | 1 |', text)                         # toujours sans type
        self.assertTrue((Path(self.project) / RT.JOURNAL).is_file())

        RT.main(['ajouter', self.project])                             # deuxieme passage : rien a faire
        RT.main(['annuler', self.project])
        undone = types_of(self.project)
        for path in EXPECTED:
            self.assertIsNone(undone[path], path)
        self.assertFalse((Path(self.project) / RT.JOURNAL).is_file())

        RT.main(['ajouter', self.project])                             # pour la suite : projet type
        self.assertEqual(types_of(self.project)['textures/qui/test_font.ttfc'], 'ttf')


def wait_until(cond, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        APP.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


class TestViewers(unittest.TestCase):
    """Panneau de droite : onglet choisi et contenu pour chaque nouveau type."""

    @classmethod
    def setUpClass(cls):
        global APP
        from PySide6.QtWidgets import QApplication
        APP = QApplication.instance() or QApplication([])
        import faux_projet
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_l2_gui_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, FILES)
        text_paths(cls.project)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp.name, ignore_errors=True)

    def setUp(self):
        from deca_gui.main import MainWindow
        self.errors = []
        self.window = MainWindow()
        self.window.error_dialog = self.errors.append
        self.window.show()
        self.window.project_open_path(str(Path(self.project) / 'project.json'))
        self.assertTrue(wait_until(lambda: not self.window.task_running() and self.window.vfs is not None),
                        'ouverture non terminee : {}'.format(self.errors))
        self.dv = self.window.ui.data_view

    def tearDown(self):
        wait_until(lambda: not self.window.task_running(), 120)
        if self.window.vfs is not None:
            self.window.tabs_nodes_clear()
            self.window.vfs.db_conn.close()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def show(self, path):
        uid = _projet.nodes_by_path(self.window.vfs, path)[0].uid
        self.window.vnode_2click_selected([uid])
        APP.processEvents()
        return self.dv.tab_widget.tabText(self.dv.tab_widget.currentIndex())

    def lines(self, tab):
        m = tab.list_view.model()
        return [m.data(m.index(i, 0)) for i in range(m.rowCount())]

    def test_tabs_and_content(self):
        dv = self.dv
        self.assertEqual(self.show('sound/sound_module_framework/test_root.smodc'), 'RTPC')
        top = dv.tab_rtpc.tree.model.root.children()[0]
        self.assertTrue(top.cols[0].startswith('RTPC (inline): '), top.cols[0])
        module = top.children()[0]
        self.assertEqual(module.cols[1], 'node: 7 props, 1 children')
        names = {it.cols[0] for it in module.children()}
        self.assertIn('name', names)

        self.assertEqual(self.show('worlds/world2/locations/hp_trophyworld/lodges/lodge_test.obc'), 'OBC')
        self.assertTrue(self.lines(dv.tab_obc)[0].startswith('OBC version 1: 2 occluder boxes'))

        self.assertEqual(self.show('editor/entities/test/weapon_test.fmod_guids'), 'Structure')
        self.assertEqual(self.lines(dv.tab_structure)[0], 'FMOD event GUIDs: 2 (version 2)')
        self.assertEqual(self.show('textures/qui/test_font.ttfc'), 'Structure')
        self.assertIn('family', ' '.join(self.lines(dv.tab_structure)))

        self.assertEqual(self.show('worlds/world4/terrain/global_heightfield.rawc'), 'Image')
        self.assertIn('rawc f32 64x64', dv.tab_image.select_dropdown.currentText())

        self.assertEqual(self.show('animations/test/rig_test.bsk'), 'Havok')
        top = dv.tab_havok.tree.model.root.children()
        self.assertTrue(top[0].cols[0].startswith('Havok packfile hk_2014.1.0-r1'), top[0].cols)
        self.assertIn('hkaSkeleton', [t.cols[0] for t in top])
        self.assertEqual(self.show('ai/bts/animals/test_flee.btc'), 'Structure')
        self.assertEqual(self.lines(dv.tab_structure)[0],
                         'Behaviour tree (btc version 18): intermediate/ai/bts/animals/test_flee.bts')
        self.assertEqual(dv.errors, [])
        self.assertEqual(self.errors, [])

    def test_inline_text_dump(self):
        from deca_gui.viewer_rtpc import inline_text
        from deca_gui.reading import noms_for
        text = inline_text(smodc_bytes(), noms_for(self.window.vfs))
        self.assertIn('base_music', text)


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
