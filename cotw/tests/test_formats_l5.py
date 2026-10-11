#!/usr/bin/env python3
"""
Tests du lot du 2026-10-10 (soir) : textures sans en-tete (deca.ff_bc_raw : .hmddsc sans .ddsc, fichiers sans nom
ni type de la taille d'une chaine de mips), .fmod_guids avec noms de banques (global_sound_layers_<reserve>) ; leur
affichage dans l'interface et leur export.

Textures fabriquees par bc_fabrique.py (images synthetiques, encodeur BC3 de cotw/icone.py) : aucun fichier du jeu
n'est versionne.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_formats_l5.py [--spawn]
"""
import io
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
import bc_fabrique as BF  # noqa: E402
import fmod_fabrique as FF  # noqa: E402

import deca.ff_bc_raw as R  # noqa: E402
import deca.ff_fmod_guids as G  # noqa: E402
import deca.ff_hikcc as HK  # noqa: E402
from deca.ff_determine import determine_file_type_and_size  # noqa: E402

APP = None

BC1_256 = BF.hmddsc('BC1', 256, 256, 3)            # 43 008 octets : aussi la taille d'une chaine 16 octets 256x128
BC5_128 = BF.hmddsc('BC5', 128, 128, 3, seed=1)     # 21 504 octets : aussi BC1 256x128, BC3 128x128
BANKS = ['animal_boar', 'music_germanfarmlands', 'reserve_germanfarmlands']


def hikcc_bytes(nodes=((0, (0, 0, 0)), (1, (0, 103.5, 0)), (15, (0.1, 165.4, -1.2)), (50, (69.8, 150.5, -7.3)))):
    """Comme animations/humanik/elmer.hikcc : magic, version 1, 172 drapeaux, un enregistrement de 120 octets par
    noeud utilise (translation, quaternion, echelle, 20 flottants a zero), 16 octets a zero."""
    flags = [0] * HK.NODE_COUNT
    for i, _t in nodes:
        flags[i] = 1
    out = struct.pack('<II', HK.MAGIC, 1) + struct.pack('<{}I'.format(HK.NODE_COUNT), *flags)
    for _i, t in nodes:
        out += struct.pack('<10f', *t, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0) + bytes(80)
    return out + bytes(16)


def layers_bytes():
    return G.to_bytes([FF.GUID_GRAVEL, FF.GUID_SHELLS], banks=BANKS)


# ---------------------------------------------------------------------------------------------------------
# Textures sans en-tete
# ---------------------------------------------------------------------------------------------------------

class TestLayouts(unittest.TestCase):
    def test_sizes_of_the_game(self):
        """Tailles des echantillons du jeu : chaines de mips completes, ambigues sans en-tete."""
        def shapes(n):
            return {(lay.block_bytes, lay.width, lay.height, lay.levels) for lay in R.layouts(n)}
        self.assertIn((8, 2048, 2048, 3), shapes(2752512))            # BC1 2048 + 1024 + 512
        self.assertIn((16, 2048, 1024, 3), shapes(2752512))           # ou blocs de 16 octets 2048x1024...
        self.assertIn((16, 2048, 2048, 4), shapes(5570560))
        self.assertIn((16, 1024, 1024, 3), shapes(1376256))
        self.assertIn((8, 512, 512, 1), shapes(131072))
        for n in (0, 6, 2610, 4652, 316208, 987185, 5116085):          # restes sans type du jeu : pas des textures
            self.assertFalse(R.is_bc_raw_size(n), n)

    def test_layout_offsets(self):
        lay = R.Layout(8, 256, 256, 3)
        self.assertEqual(lay.total(), 43008)
        self.assertEqual([lay.level_offset(i) for i in range(3)], [10240, 2048, 0])   # le plus petit d'abord

    def test_guess_ranks_the_right_layout_first(self):
        cases = [(BC1_256, 'BC1', 256, 256, 3), (BC5_128, 'BC5', 128, 128, 3),
                 (BF.hmddsc('BC3', 128, 128, 3, seed=2), 'BC3', 128, 128, 3),
                 (BF.hmddsc('BC1', 256, 128, 2, seed=3), 'BC1', 256, 128, 2),
                 (BF.hmddsc('BC5', 64, 256, 1, seed=4), 'BC5', 64, 256, 1)]
        for data, fmt, w, h, k in cases:
            g = R.guess(data)
            self.assertGreater(len(g), 2)
            best = g[0]
            self.assertEqual((best.format_name, best.layout.width, best.layout.height, best.layout.levels),
                             (fmt, w, h, k), R.describe_guesses(g))
            self.assertLess(best.score, g[1].score)

    def test_decoded_level_is_the_image(self):
        """Le niveau decode avec la bonne disposition est proche de l'image encodee (ecart moyen faible)."""
        import numpy as np
        g = R.guess(BC1_256)[0]
        img = R.decode_level(BC1_256, g.layout, g.fmt)
        ref = BF.image(256, 256)
        self.assertLess(float(np.abs(img[:, :, :3].astype(int) - ref[:, :, :3].astype(int)).mean()), 6.0)
        small = R.decode_level(BC1_256, g.layout, g.fmt, 2)
        self.assertEqual(small.shape[:2], (64, 64))

    def test_bc7_only_when_nothing_is_plausible(self):
        tried = []
        real = R._decode

        def spy(raw, w, h, fmt):
            tried.append(fmt)
            return real(raw, w, h, fmt)
        R._decode = spy
        try:
            R.guess(BC1_256)
            self.assertNotIn(R.BC7, tried)
            tried.clear()
            noise = bytes((i * 7919 + (i >> 3) * 104729) % 251 for i in range(43008))
            g = R.guess(noise)
            self.assertIn(R.BC7, tried)
            self.assertGreater(g[0].score, R.BC7_ONLY_ABOVE * 0.5)
        finally:
            R._decode = real

    def test_dds_and_png(self):
        g = R.guess(BC1_256)[0]
        dds = R.dds_bytes(BC1_256, g)
        self.assertEqual(dds[:4], b'DDS ')
        size, flags, h, w, linear, _depth, mips = struct.unpack_from('<7I', dds, 4)
        self.assertEqual((size, h, w, linear, mips), (124, 256, 256, 32768, 3))
        self.assertEqual(dds[84:88], b'DX10')
        self.assertEqual(struct.unpack_from('<5I', dds, 128), (71, 3, 0, 1, 0))
        body = dds[148:]
        self.assertEqual(body[:32768], BC1_256[-32768:])               # le plus grand niveau en premier
        self.assertEqual(body[-2048:], BC1_256[:2048])
        from PIL import Image
        self.assertEqual(Image.open(io.BytesIO(R.png_bytes(BC1_256, g))).size, (256, 256))
        png, dds_name = R.export_names('x/y.hmddsc', g)
        self.assertEqual(png, 'x/y.hmddsc.DECA.guess_BC1_256x256.png')
        self.assertEqual(dds_name, 'x/y.hmddsc.DECA.guess_BC1_256x256.dds')

    def test_not_typed_at_scan(self):
        """Pas de type pour ces donnees : un nom trouve plus tard (x.hmddsc) les type comme le fait DECA."""
        self.assertIsNone(determine_file_type_and_size(io.BytesIO(BC1_256), len(BC1_256), False)[0])


# ---------------------------------------------------------------------------------------------------------
# .fmod_guids avec noms de banques
# ---------------------------------------------------------------------------------------------------------

class TestFmodGuidsBanks(unittest.TestCase):
    def test_round_trip_and_detection(self):
        data = layers_bytes()
        version, banks, guids = G.parse(data)
        self.assertEqual((version, banks, guids), (2, BANKS, [FF.GUID_GRAVEL, FF.GUID_SHELLS]))
        self.assertEqual(G.to_bytes(guids, banks=banks), data)
        self.assertEqual(data[:8], bytes.fromhex('0200000003000b00'))   # u32 2, u16 3 banques, u16 11 (longueur)
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'fmodguids')
        plain = G.to_bytes([FF.GUID_GRAVEL])
        self.assertEqual(plain[4:8], b'\x00\x00\x01\x00')                 # sans banque : la disposition d'avant
        self.assertEqual(G.parse(plain)[1], [])

    def test_empty_bank_name(self):
        """global/global_sound_layers.fmod_guids : 23 banques dont une au nom vide."""
        data = G.to_bytes([FF.GUID_GRAVEL], banks=['gui', '', 'weapons'])
        self.assertEqual(G.parse(data)[1], ['gui', '', 'weapons'])
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'fmodguids')
        self.assertIn('  (empty name)', G.dump_lines(data))

    def test_rejects(self):
        data = layers_bytes()
        for bad in (data[:-1], data + b'\0', data[:6] + b'\x05\x00ab' + data[10:]):
            self.assertFalse(G.is_fmod_guids_data(bad))
            with self.assertRaises(ValueError):
                G.parse(bad)
        self.assertIsNone(determine_file_type_and_size(io.BytesIO(data[:-1]), len(data) - 1, False)[0])

    def test_dump_with_bank_paths(self):
        names = {FF.GUID_GRAVEL: 'event:/weapons/bullet_impact/plr_bullet_impact_gravel',
                 uuid.UUID(int=99): 'bank:/animals/specific/animal_boar'}
        lines = G.dump_lines(layers_bytes(), names)
        self.assertEqual(lines[0], 'FMOD event GUIDs: 2 (version 2, 3 bank(s))')
        self.assertEqual(lines[1:5], ['banks:', '  animal_boar  bank:/animals/specific/animal_boar',
                                      '  music_germanfarmlands', '  reserve_germanfarmlands'])
        self.assertEqual(lines[5], 'events:')
        self.assertTrue(lines[6].endswith('event:/weapons/bullet_impact/plr_bullet_impact_gravel'))


class TestHikcc(unittest.TestCase):
    def test_read_and_dump(self):
        data = hikcc_bytes()
        self.assertEqual(len(data), 8 + 4 * 172 + 4 * 120 + 16)
        h = HK.Hikcc(data)
        self.assertEqual([(n.index, n.name) for n in h.nodes],
                         [(0, 'Reference'), (1, 'Hips'), (15, 'Head'), (50, 'LeftHandThumb1')])
        self.assertAlmostEqual(h.nodes[1].translation[1], 103.5, places=4)
        self.assertEqual(h.nodes[2].rotation, (0.0, 0.0, 0.0, 1.0))
        lines = HK.dump_lines(data)
        self.assertEqual(lines[0], 'HumanIK character definition (version 1): 4 nodes used of 172')
        self.assertTrue(lines[-1].startswith(' 50 LeftHandThumb1'))
        self.assertEqual(HK.node_name(74), 'RightHandThumb1')
        self.assertEqual(HK.node_name(93), 'RightHandPinky4')
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'hikcc')

    def test_rejects(self):
        data = hikcc_bytes()
        for bad in (data[:-1], data + bytes(120), data[:12] + b'\x02' + data[13:]):
            with self.assertRaises(HK.EHikcc):
                HK.Hikcc(bad)


# ---------------------------------------------------------------------------------------------------------
# Interface et export
# ---------------------------------------------------------------------------------------------------------

FILES = [
    ('models/test/textures/wood_test_dif.hmddsc', BC1_256),           # pas de .ddsc : mip orphelin
    ('models/test/textures/unnamed_test_nrm.hmddsc', BC5_128),         # nom efface apres le scan : sans nom ni type
    ('worlds/base/locations/test/global_sound_layers_test.fmod_guids', layers_bytes()),
    ('sound/fmod_banks/master_strings.fmod_bankc', FF.strings_bank()),
    ('animations/humanik/test.hikcc', hikcc_bytes()),
]
UNNAMED = 'models/test/textures/unnamed_test_nrm.hmddsc'


def prepare(project):
    """v_path en texte (comme un vrai projet) ; type hmddsc par l'extension, comme le scan le donne a un .hmddsc
    nomme (faux_projet nomme les fichiers apres le scan) ; le second .hmddsc perd son nom et son type, comme les
    1 457 blocs sans nom du jeu."""
    db = sqlite3.connect(os.path.join(project, 'db', 'core.db'))
    db.execute("UPDATE core_nodes SET v_path = CAST(v_path AS TEXT) WHERE typeof(v_path) = 'blob'")
    db.execute("UPDATE core_nodes SET file_type = 'hmddsc' WHERE v_path LIKE '%.hmddsc'")
    db.execute('UPDATE core_nodes SET v_path = NULL, file_type = NULL WHERE v_path = ?', (UNNAMED,))
    db.commit()
    db.close()


def wait_until(cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


class TestViewersAndExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        from PySide6.QtWidgets import QApplication
        APP = QApplication.instance() or QApplication([])
        import faux_projet
        from deca.hashes import hash32_func
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_l5_gui_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, FILES)
        prepare(cls.project)
        cls.unnamed_hash = int(hash32_func(UNNAMED.encode()))

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

    def unnamed_node(self):
        nodes = [n for n in self.window.vfs.nodes_where_match(v_hash=self.unnamed_hash) if n.offset is not None]
        self.assertEqual(len(nodes), 1)
        self.assertIsNone(nodes[0].file_type)
        return nodes[0]

    def show_uid(self, uid):
        self.window.vnode_2click_selected([uid])
        APP.processEvents()
        return self.dv.tab_widget.tabText(self.dv.tab_widget.currentIndex())

    def dropdown(self):
        d = self.dv.tab_image.select_dropdown
        return [d.itemText(i) for i in range(d.count())]

    def test_tabs_and_content(self):
        dv = self.dv
        uid = _projet.nodes_by_path(self.window.vfs, 'models/test/textures/wood_test_dif.hmddsc')[0].uid
        self.assertEqual(self.show_uid(uid), 'Image')
        items = self.dropdown()
        self.assertTrue(items[0].startswith('256x256 (BC1, largest level 0, best guess'), items)
        self.assertEqual(len([x for x in items if 'best guess' in x]), 3)   # les 3 niveaux de la meilleure
        self.assertTrue(any('guess 2/' in x for x in items))                 # puis les autres dispositions
        self.assertEqual(dv.tab_image.ddsc.mips[0].data.shape[:2], (256, 256))

        self.assertEqual(self.show_uid(self.unnamed_node().uid), 'Image')
        self.assertTrue(self.dropdown()[0].startswith('128x128 (BC5, largest level 0, best guess'), self.dropdown())

        path = 'worlds/base/locations/test/global_sound_layers_test.fmod_guids'
        uid = _projet.nodes_by_path(self.window.vfs, path)[0].uid
        self.assertEqual(self.show_uid(uid), 'Structure')
        m = dv.tab_structure.list_view.model()
        lines = [m.data(m.index(i, 0)) for i in range(m.rowCount())]
        self.assertEqual(lines[0], 'FMOD event GUIDs: 2 (version 2, 3 bank(s))')
        self.assertIn('event:/weapons/bullet_impact/plr_bullet_impact_gravel', ' '.join(lines))

        uid = _projet.nodes_by_path(self.window.vfs, 'animations/humanik/test.hikcc')[0].uid
        self.assertEqual(self.show_uid(uid), 'Structure')
        m = dv.tab_structure.list_view.model()
        self.assertEqual(m.data(m.index(0, 0)), 'HumanIK character definition (version 1): 4 nodes used of 172')
        self.assertEqual(dv.errors, [])
        self.assertEqual(self.errors, [])

    def test_export_processed(self):
        from deca.export_import import node_export_guessed_texture
        out = Path(self.tmp.name) / 'export'
        vfs = self.window.vfs
        named = _projet.nodes_by_path(vfs, 'models/test/textures/wood_test_dif.hmddsc')[0]
        node_export_guessed_texture(vfs, named, str(out))
        node_export_guessed_texture(vfs, self.unnamed_node(), str(out))
        files = sorted(str(p.relative_to(out)).replace('\\', '/') for p in out.rglob('*') if p.is_file())
        self.assertEqual(files, ['{:08X}.dat.DECA.guess_BC5_128x128.dds'.format(self.unnamed_hash),
                                 '{:08X}.dat.DECA.guess_BC5_128x128.png'.format(self.unnamed_hash),
                                 'models/test/textures/wood_test_dif.hmddsc.DECA.guess_BC1_256x256.dds',
                                 'models/test/textures/wood_test_dif.hmddsc.DECA.guess_BC1_256x256.png'])

    def test_types_after_scan(self):
        db = sqlite3.connect(os.path.join(self.project, 'db', 'core.db'))
        rows = {_projet.to_text(v): _projet.to_text(t) for v, t in
                db.execute('SELECT v_path, file_type FROM core_nodes WHERE v_path IS NOT NULL').fetchall()}
        db.close()
        self.assertEqual(rows['models/test/textures/wood_test_dif.hmddsc'], 'hmddsc')
        self.assertEqual(rows['worlds/base/locations/test/global_sound_layers_test.fmod_guids'], 'fmodguids')
        self.assertEqual(rows['animations/humanik/test.hikcc'], 'hikcc')


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
