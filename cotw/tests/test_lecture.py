#!/usr/bin/env python3
"""
Tests des visionneuses de lecture de l'interface (deca_gui) : arbres paresseux RTPC / ADF, tables StringLookup et
XLSBook, entrees SARC, routage des onglets du panneau de droite.

Les tests sans projet lisent les octets fabriques par cotw/demo_projet.py (memes generateurs que le projet demo) ;
les tests de routage ouvrent le projet demo, construit par un vrai scan dans un dossier temporaire.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_lecture.py [--spawn]
"""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402  (substitut de zugbruecke hors Windows)
import demo_projet  # noqa: E402
import noms as N  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from deca_gui import reading, tree_items as T  # noqa: E402

APP = None
SARC_243_SP = 'editor/entities/hp_weapons/ammunition/rifles/equipment_ammo_243_sp_01.ee'


def setUpModule():
    # cree ici et pas a l'import : les processus du scan (multiprocessing "spawn") reimportent ce module
    global APP
    APP = QApplication.instance() or QApplication([])


def find(item, pred, limit=100000):
    for it in item.walk(limit):
        if pred(it):
            return it
    return None


def by_name(item, name):
    return find(item, lambda it: it.cols[0] == name)


def read_adf(data: bytes):
    """Adf lu par DECA sans projet (les fichiers fabriques portent leurs types)."""
    from deca.ff_adf import Adf
    from deca.file import ArchiveFile
    base = 5 if data[5:9] == b' FDA' else 0
    adf = Adf()
    adf.deserialize(ArchiveFile(io.BytesIO(data[base:])))
    return adf


def demo_noms(*names):
    """Noms sans projet : dictionnaire du fork, plus les noms donnes (ce que core_strings apporterait au scan)."""
    noms = N.Noms(vfs=None, use_dictionary=True)
    noms.register(names)
    return noms


GROUPS = ('ammo', 'weapons', 'sights')   # noms de noeuds absents des valeurs du fichier : connus par le scan
FIELDS = ('name', 'locked')              # noms de proprietes : field_strings de DECA, lus au scan


class TestTreeItem(unittest.TestCase):
    def test_lazy_loading(self):
        calls = []

        def load():
            calls.append(1)
            return [T.TreeItem(['a']), T.TreeItem(['b'])]
        it = T.TreeItem(['x'], load=load)
        self.assertTrue(it.has_children())
        self.assertFalse(it.loaded())
        self.assertEqual(calls, [])
        kids = it.children()
        self.assertEqual([k.cols[0] for k in kids], ['a', 'b'])
        self.assertEqual([(k.parent, k.row) for k in kids], [(it, 0), (it, 1)])
        it.children()
        self.assertEqual(calls, [1], 'children loaded once')
        self.assertEqual(len(it.cols), len(T.COLUMNS))

    def test_ranges(self):
        leaf = lambda i: T.TreeItem([str(i)])  # noqa: E731
        self.assertEqual(len(T.ranges(256, leaf)), 256)
        top = T.ranges(1000, leaf)
        self.assertEqual([r.cols[0] for r in top], ['[0 .. 255]', '[256 .. 511]', '[512 .. 767]', '[768 .. 999]'])
        self.assertEqual(top[3].children()[-1].cols[0], '999')
        big = T.ranges(70000, leaf)                     # 256^2 < 70000 : deux niveaux de tranches
        self.assertEqual([r.cols[0] for r in big], ['[0 .. 65535]', '[65536 .. 69999]'])
        self.assertEqual(len(big[0].children()), 256)
        self.assertEqual(big[1].children()[0].cols[0], '[65536 .. 65791]')
        self.assertEqual(big[1].children()[-1].children()[-1].cols[0], '69999')
        self.assertTrue(all(len(it.children()) <= T.CHUNK for it in T.root(lambda: big).walk(3000)))

    def test_byte_rows(self):
        data = b'equipment_ammo\0' + bytes(range(100))
        rows = T.byte_rows(data, 0x40)
        self.assertEqual(len(rows), (len(data) + T.BYTES_PER_ROW - 1) // T.BYTES_PER_ROW)
        self.assertTrue(rows[0].cols[2].startswith('65 71 75'))
        self.assertIn('equipment_ammo.', rows[0].cols[2])
        self.assertEqual(rows[1].cols[3], '@0x{:x}'.format(0x40 + T.BYTES_PER_ROW))
        self.assertEqual(T.byte_rows(b'abc', None)[0].cols[3], '+0x0')

    def test_walk_limit_and_text(self):
        tree = T.root(lambda: T.ranges(1000, lambda i: T.TreeItem([str(i), 'u32', str(i * 2)])))
        self.assertEqual(sum(1 for _ in tree.walk(10)), 10)
        lines = tree.children()[0].text_lines()
        self.assertEqual(lines[0], '[0 .. 255]  256 elements')
        self.assertEqual(lines[1], '  0  u32  0')
        self.assertEqual(len(tree.children()[0].text_lines(limit=5)), 5)


class TestValueNote(unittest.TestCase):
    def test_notes(self):
        self.assertEqual(reading.value_note('secondary_ids', None, '"-1"'), 'base game')
        self.assertEqual(reading.value_note('secondary_ids', None, '"3988076135"'), 'DLC id, given to every player')
        self.assertEqual(reading.value_note('secondary_ids', None, '"1095272277"'), 'DLC id')
        self.assertEqual(reading.value_note('equipment_x', 'locked', '5 (0x00000005)'),
                         'hidden from the store (base game item)')
        self.assertEqual(reading.value_note('equipment_x', 'locked', '1 (0x00000001)'), '')
        self.assertEqual(reading.value_note('equipment_x', 'name', '"-1"'), '')


class TestRtpcTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import deca.ff_rtpc_write as W
        cls.tree = T.rtpc_tree(W.rtpc_from_bytes(demo_projet.equipment_data()), demo_noms(*GROUPS, *FIELDS))

    def test_root_and_names(self):
        top = self.tree.children()[0]
        self.assertTrue(top.cols[0].startswith('RTPC v2: '))
        self.assertEqual([c.cols[0] for c in top.children()], ['ammo', 'weapons', 'sights'])
        ammo = top.children()[0]
        self.assertEqual(ammo.cols[1], 'node: 0 props, {} children'.format(len(demo_projet.AMMO)))
        self.assertEqual(ammo.cols[3].split()[0], '0x{:08x}'.format(N.hash32('ammo')))

    def test_props_and_notes(self):
        item = by_name(self.tree, 'equipment_ammo_243_pt_01')
        props = {c.cols[0]: c for c in item.children()}
        self.assertEqual(props['cash_price'].cols[1:3], ['u32', '45 (0x0000002d)'])
        self.assertEqual(props['name'].cols[2], '"equipment_ammo_243_pt_01"')
        sec = props['secondary_ids'].children()[0]
        self.assertEqual(sec.cols[0], '0xa4f0837b')            # nom inconnu : le hash
        self.assertEqual(sec.cols[2], '"-1"   [base game]')
        hidden = by_name(self.tree, 'equipment_ammo_762x54r_fmj_01')
        locked = [c for c in hidden.children() if c.cols[0] == 'locked'][0]
        self.assertIn('[hidden from the store (base game item)]', locked.cols[2])
        compat = by_name(self.tree, 'compatibility_1')
        self.assertIsNotNone(compat, 'compatibility_N nomme par hp_noms.tsv')

    def test_names_from_file_strings(self):
        """Sans projet ni nom enregistre : un noeud est nomme par une valeur chaine du fichier (sa propriete name)."""
        import deca.ff_rtpc_write as W
        tree = T.rtpc_tree(W.rtpc_from_bytes(demo_projet.equipment_data()), demo_noms())
        ammo = tree.children()[0].children()[0]
        self.assertEqual(ammo.cols[0], '0x{:08x}'.format(N.hash32('ammo')))   # aucune chaine "ammo" dans le fichier
        self.assertEqual(ammo.children()[0].cols[0], 'equipment_ammo_243_sp_01')
        rtpc = W.rtpc_from_bytes(demo_projet.equipment_data())
        local = T.file_strings(rtpc)
        self.assertEqual(local[N.hash32('equipment_ammo_243_pt_01')], ['equipment_ammo_243_pt_01'])
        # le nom du projet passe avant celui du fichier
        tree2 = T.rtpc_tree(rtpc, demo_noms('ammo', 'zzz_other'))
        self.assertEqual(tree2.children()[0].children()[0].cols[0], 'ammo')

    def test_class_hint(self):
        import deca.ff_rtpc_write as W
        sarc = __import__('sarc')
        epe = [e for e in sarc.parse(demo_projet.entity('equipment_ammo_243_sp_01')).entries
               if e.path.endswith('.epe')][0]
        tree = T.rtpc_tree(W.rtpc_from_bytes(epe.data), demo_noms())
        self.assertTrue(tree.children()[0].cols[0].startswith('RTPC v1: '))
        node = tree.children()[0].children()[0]
        self.assertEqual(node.cols[0], 'CAmmunitionItem')        # nom tire de la valeur de _class
        self.assertEqual(node.cols[2], 'CAmmunitionItem')


class TestAdfTree(unittest.TestCase):
    def test_xlsbook(self):
        adf = read_adf(demo_projet.stats_ui())
        tree = T.adf_tree(adf, demo_noms(), 'settings/hp_settings/equipment_stats_ui.bin')
        top = tree.children()[0]
        self.assertEqual(top.cols[:2], ['ADF settings/hp_settings/equipment_stats_ui.bin', '1 instances'])
        ins = top.children()[0]
        self.assertTrue(ins.cols[0].startswith('instance '))
        self.assertTrue(ins.cols[3].startswith('0x{:08x}'.format(reading.XLSBOOK_TYPE)))
        members = {c.cols[0]: c for c in ins.children()}
        for m in ('Sheet', 'Cell', 'StringData', 'ValueData', 'BoolData'):
            self.assertIn(m, members)
        self.assertEqual(members['Sheet'].cols[2], '2 elements')
        sheet0 = members['Sheet'].children()[0]
        name = [c for c in sheet0.children() if c.cols[0] == 'Name'][0]
        self.assertIn('"ammo"', name.cols[2])
        strings = '\n'.join(members['StringData'].text_lines())
        self.assertIn('equipment_ammo_243_sp_01', strings)

    def test_stringlookup_text_block_in_ranges(self):
        data = demo_projet.string_ids()
        adf = read_adf(data)
        tree = T.adf_tree(adf, demo_noms())
        ins = tree.children()[0].children()[0]
        self.assertTrue(ins.cols[3].startswith('0x{:08x}'.format(reading.STRINGLOOKUP_TYPE)))
        text = [c for c in ins.children() if c.cols[0] == 'Text'][0]
        self.assertRegex(text.cols[2], r'^\d+ (elements|bytes)')
        rows = text.children()
        self.assertTrue(0 < len(rows) <= T.CHUNK)
        self.assertTrue(any('equipment_' in r.cols[2] for r in rows[:4]) or
                        any('equipment_' in c.cols[2] for r in rows[:1] for c in r.children()[:4]))
        pairs = find(ins, lambda it: it.cols[0] == 'Hash')
        self.assertIsNotNone(pairs)
        # les hash32 des cles connues du dictionnaire / de core_strings sont annotes
        key = demo_projet.display_key('equipment_ammo_243_sp_01', '_name')
        tree2 = T.adf_tree(read_adf(data), demo_noms(key))
        hit = find(tree2, lambda it: it.cols[0] == 'Hash' and it.cols[2].endswith('= hash of "{}"'.format(key)),
                   limit=50000)
        self.assertIsNotNone(hit, 'Hash de SortedPairs annote "= hash of ..."')
        self.assertTrue(hit.cols[2].startswith(str(N.hash32(key))))


class TestAdfInstanceTypes(unittest.TestCase):
    def test_types(self):
        self.assertEqual(reading.adf_instance_types(demo_projet.string_ids()), [reading.STRINGLOOKUP_TYPE])
        self.assertEqual(reading.adf_instance_types(demo_projet.stats_ui()), [reading.XLSBOOK_TYPE])
        prefixed = b'\x01\x01\x00\x00\x00' + demo_projet.stats_ui()     # ADF5 (sauvegardes, .ee)
        self.assertEqual(reading.adf_instance_types(prefixed), [reading.XLSBOOK_TYPE])
        self.assertEqual(reading.adf_instance_types(b'RTPC' + b'\0' * 60), [])
        self.assertEqual(reading.adf_instance_types(b' FDA' + b'\xff' * 20), [])


class TestStringTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deca_gui.viewer_stringlookup import StringTable
        cls.table = StringTable(demo_projet.string_ids())

    def test_keys_and_texts(self):
        t = self.table
        self.assertEqual(t.languages, demo_projet.LANGUAGES)
        keys = {t.key(h) for h in t.hashes}
        self.assertEqual(len(keys), len(t.hashes))
        self.assertEqual(len(t.key_offsets), len(t.hashes), 'cles lues sur les lignes de dialogue')
        key = demo_projet.display_key('equipment_ammo_243_sp_01', '_name')
        self.assertIn(key, keys)
        h = N.hash32(key)
        self.assertIn(h, t.hashes)
        self.assertEqual(t.key(h), key)
        self.assertIsNotNone(t.text(h, 0))
        self.assertIsNotNone(t.text(h, 1))

    def test_matching(self):
        t = self.table
        self.assertEqual(t.matching(''), list(range(len(t.hashes))))
        french = t.matching('carabine')                   # texte francais seulement
        self.assertTrue(french)
        for r in french:
            self.assertIn('carabine', (t.text(t.hashes[r], 1) or '').lower())
        h = t.hashes[3]
        self.assertEqual(t.matching('{:08x}'.format(h)), [3])
        self.assertEqual(t.matching('ZZZ-nothing'), [])

    def test_keys_when_lines_are_not_parallel(self):
        """Une ligne de dialogue en moins (ex. collision de hash32) ne fait plus perdre toutes les cles."""
        import stringlookup as SL
        from deca_gui.viewer_stringlookup import StringTable
        orig = SL.Vue.lines
        SL.Vue.lines = lambda vue, lang: orig(vue, lang)[1:]   # la ligne du plus petit hash manque partout
        try:
            t = StringTable(demo_projet.string_ids())
        finally:
            SL.Vue.lines = orig
        self.assertEqual(len(t.key_offsets), len(t.hashes) - 1)
        self.assertEqual(t.key(t.hashes[0]), '')
        self.assertEqual(N.hash32(t.key(t.hashes[1])), t.hashes[1])

    def test_key_from_names_when_no_dialogue_lines(self):
        from deca_gui.viewer_stringlookup import StringTable
        noms = demo_noms()
        noms.register(['some_key'])
        t = StringTable(demo_projet.string_ids(), noms)
        t.key_offsets = {}
        self.assertEqual(t.key(N.hash32('some_key')), 'some_key')
        self.assertEqual(t.key(1), '')


class TestXlsBook(unittest.TestCase):
    def test_sheets(self):
        from deca_gui.viewer_xlsbook import XlsBook, text_of
        adf = read_adf(demo_projet.stats_ui())
        book = XlsBook(adf.table_instance_values[0])
        self.assertEqual(book.names, ['ammo', 'weapons'])
        rows = book.rows(0)
        self.assertEqual(rows[0][0], 'Name')
        self.assertEqual(len(rows), 1 + len(demo_projet.AMMO) + demo_projet.STATS_EMPTY_ROWS)
        self.assertEqual(book.first_empty_row(0), 1 + len(demo_projet.AMMO))
        self.assertEqual(text_of(True), '')
        self.assertEqual(text_of(150.0), '150')
        self.assertEqual(text_of(0.25), '0.25')
        self.assertEqual(text_of(None), '')


class TestSarcRows(unittest.TestCase):
    def test_entry_rows(self):
        from deca_gui.viewer_sarc import entry_rows
        rows, version = entry_rows(demo_projet.entity('equipment_ammo_243_sp_01'))
        self.assertEqual(version, 2)
        self.assertEqual([r[0] for r in rows], [0, 1, 2])
        exts = [os.path.splitext(r[1])[1] for r in rows]
        self.assertEqual(exts, ['.fmod_guids', '.ammotunec', '.epe'])
        self.assertEqual([r[4] for r in rows], ['(unknown)', 'adf', 'rtpc'])
        self.assertEqual(rows[0][3], 8)
        self.assertFalse(any(r[5] for r in rows))
        with self.assertRaises(Exception):
            entry_rows(b'not a sarc')


class TestWidgetsOffscreen(unittest.TestCase):
    """Widgets seuls, sans projet."""

    def test_tree_viewer(self):
        from deca_gui.tree_view import TreeViewer
        import deca.ff_rtpc_write as W
        tv = TreeViewer()
        calls = []
        tv.set_tree(T.rtpc_tree(W.rtpc_from_bytes(demo_projet.equipment_data()), demo_noms(*GROUPS)),
                    lambda: calls.append(1) or 'DUMP', expand_depth=2)
        m = tv.model
        self.assertEqual(m.rowCount(), 1)
        top = m.index(0, 0)
        self.assertEqual(m.rowCount(top), 3)
        self.assertEqual(m.data(m.index(0, 0, top)), 'ammo')
        self.assertEqual(m.parent(m.index(0, 0, top)), top)
        # recherche : charge les enfants en chemin, repart du debut a la fin
        tv.find_edit.setText('equipment_sight_scope_4x32_01')
        hit = tv.find_next()
        self.assertEqual(hit.cols[0], 'equipment_sight_scope_4x32_01')
        self.assertEqual(tv.view.currentIndex().internalPointer(), hit)
        self.assertIsNotNone(tv.find_next())                 # un autre champ (name) contient le texte
        tv.find_edit.setText('ZZZ-nothing')
        self.assertIsNone(tv.find_next())
        # export texte
        path = os.path.join(tempfile.mkdtemp(prefix='cotwed_lect_'), 'rtpc.txt')
        try:
            tv.export_text(path)
            text = Path(path).read_text(encoding='utf-8')
            self.assertIn('cash_price  u32  45 (0x0000002d)', text)
            self.assertIn('[base game]', text)
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        # vidage DECA : calcule une seule fois, a la demande
        self.assertEqual(calls, [])
        tv.raw_check.setChecked(True)
        tv.raw_check.setChecked(False)
        tv.raw_check.setChecked(True)
        self.assertEqual(calls, [1])
        self.assertEqual(tv.raw_text.toPlainText(), 'DUMP')
        self.assertEqual(tv.stack.currentIndex(), 1)

    def test_string_viewer(self):
        from deca_gui.viewer_stringlookup import DataViewerStringLookup
        v = DataViewerStringLookup()
        v.load(demo_projet.string_ids())
        n = len(v.table.hashes)
        self.assertEqual(v.model.rowCount(), n)
        self.assertEqual(v.model.columnCount(), 2 + len(demo_projet.LANGUAGES))
        self.assertIn('{} keys, 10 languages'.format(n), v.status.text())
        v.filter_edit.setText('carabine')
        v.apply_filter()
        self.assertTrue(0 < v.model.rowCount() < n)
        v.view.setCurrentIndex(v.model.index(0, 0))
        self.assertIn('fr  ', v.detail.toPlainText())
        v.language_combo.setCurrentIndex(2)                  # francais seul
        hidden = [v.view.isColumnHidden(c) for c in range(v.model.columnCount())]
        self.assertEqual(hidden, [False, False, True, False] + [True] * 8)

    def test_string_viewer_sorting(self):
        """Tri fait par le modele (pas de QSortFilterProxyModel), garde apres un filtre."""
        from PySide6.QtCore import Qt
        from deca_gui.viewer_stringlookup import DataViewerStringLookup
        v = DataViewerStringLookup()
        v.load(demo_projet.string_ids())
        m = v.model

        def column(c):
            return [m.data(m.index(r, c)) for r in range(m.rowCount())]
        keys = column(0)
        self.assertEqual(keys, sorted(keys, key=str.lower), 'trie par cle a l\'ouverture')
        v.view.sortByColumn(0, Qt.DescendingOrder)
        self.assertEqual(column(0), sorted(keys, key=str.lower, reverse=True))
        v.view.sortByColumn(1, Qt.AscendingOrder)
        self.assertEqual(column(1), sorted(column(1)))
        v.view.sortByColumn(3, Qt.AscendingOrder)              # francais
        self.assertEqual(column(3), sorted(column(3), key=str.lower))
        v.filter_edit.setText('_name')
        v.apply_filter()
        self.assertTrue(0 < m.rowCount() < len(keys))
        self.assertEqual(column(3), sorted(column(3), key=str.lower), 'tri garde apres filtre')
        self.assertTrue(all(k.endswith('_name') for k in column(0)))

    def test_sheet_viewer(self):
        from deca_gui.viewer_xlsbook import DataViewerXlsBook
        v = DataViewerXlsBook()
        v.load(read_adf(demo_projet.stats_ui()).table_instance_values[0])
        self.assertEqual(v.sheet_combo.count(), 2)
        n_ammo = len(demo_projet.AMMO)
        self.assertEqual(v.table.rowCount(), n_ammo + demo_projet.STATS_EMPTY_ROWS)
        self.assertEqual(v.table.horizontalHeaderItem(0).text(), 'Name')
        self.assertEqual(v.table.horizontalHeaderItem(6).text(), '#6')      # en-tete vide (cellule booleenne)
        self.assertEqual(v.table.item(0, 0).text(), 'equipment_ammo_243_sp_01')
        self.assertEqual(v.table.item(n_ammo, 0).text(), '')                 # ligne vide
        self.assertIn('test12 H', v.table.item(n_ammo, 0).toolTip())
        self.assertNotEqual(v.table.item(n_ammo, 0).foreground().color(), v.table.item(0, 0).foreground().color())
        self.assertIn('first empty row is row {}'.format(n_ammo + 1), v.note.text())
        v.sheet_combo.setCurrentIndex(1)
        self.assertEqual(v.table.rowCount(), len(demo_projet.WEAPONS))
        self.assertEqual(v.note.text(), '{} rows, no empty row.'.format(len(demo_projet.WEAPONS)))


def wait_until(predicate, timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    APP.processEvents()
    return predicate()


class TestRouting(unittest.TestCase):
    """Panneau de droite sur le projet demo : onglet choisi, onglets differes, erreurs rapportees."""
    root = None
    project_file = None

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='cotwed_lect_'))
        cls.project_file = demo_projet.build(cls.root / 'demo')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        from deca_gui.main import MainWindow
        self.errors = []
        self.window = MainWindow()
        self.window.error_dialog = self.errors.append
        self.window.resize(1400, 850)
        self.window.show()
        self.window.project_open_path(str(self.project_file))
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

    def uid(self, path):
        return _projet.nodes_by_path(self.window.vfs, path)[0].uid

    def show(self, path):
        self.window.vnode_2click_selected([self.uid(path)])
        APP.processEvents()
        return self.dv.tab_widget.tabText(self.dv.tab_widget.currentIndex())

    def pending(self):
        return sorted(self.dv.tab_widget.tabText(i) for i in self.dv._pending)

    def test_tabs(self):
        dv = self.dv
        self.assertEqual(self.show('settings/hp_settings/equipment_data.bin'), 'RTPC')
        self.assertEqual(self.pending(), [])
        self.assertEqual(dv.tab_rtpc.tree.model.root.children()[0].children()[0].cols[0], 'ammo')

        self.assertEqual(self.show('settings/hp_settings/string_ids.stringlookup'), 'Strings')
        self.assertEqual(self.pending(), ['ADF'])            # arbre generique : calcule a l'affichage seulement
        self.assertTrue(dv.tab_widget.isTabEnabled(dv.tab_adf_index))
        self.assertGreater(dv.tab_strings.model.rowCount(), 0)
        dv.tab_widget.setCurrentIndex(dv.tab_adf_index)
        APP.processEvents()
        self.assertEqual(self.pending(), [])
        top = dv.tab_adf.tree.model.root.children()[0]
        self.assertTrue(top.cols[0].startswith('ADF settings/hp_settings/string_ids.stringlookup'))

        self.assertEqual(self.show('settings/hp_settings/equipment_stats_ui.bin'), 'Sheet')
        self.assertEqual(self.pending(), ['ADF'])
        self.assertFalse(dv.tab_widget.isTabEnabled(dv.tab_strings_index), 'onglet du fichier precedent')

        self.assertEqual(self.show(SARC_243_SP), 'SARC')
        table = dv.tab_sarc.table
        paths = [table.item(r, 1).text() for r in range(table.rowCount())]
        self.assertEqual(len(paths), 3)
        self.assertIn('SARC v2: 3 entries, 0 links', dv.tab_sarc.summary.text())
        dv.tab_sarc._double_clicked([i for i, p in enumerate(paths) if p.endswith('.epe')][0], 1)
        APP.processEvents()
        self.assertEqual(dv.tab_widget.tabText(dv.tab_widget.currentIndex()), 'RTPC')
        epe = dv.tab_rtpc.tree.model.root.children()[0].children()[0]
        self.assertEqual(epe.cols[2], 'CAmmunitionItem')
        self.assertEqual(dv.errors, [])
        self.assertEqual(self.errors, [])

    def test_viewer_error_reported_in_info(self):
        dv = self.dv

        def broken(vfs, vnode):
            raise ValueError('boom from the test')
        dv.tab_rtpc.vnode_process = broken
        self.assertEqual(self.show('settings/hp_settings/equipment_data.bin'), 'RTPC')
        self.assertEqual(len(dv.errors), 1)
        self.assertIn('RTPC viewer failed', dv.errors[0])
        self.assertIn('boom from the test', dv.tab_info.text_box.toPlainText())
        # le noeud suivant repart sans erreur
        self.assertEqual(self.show('settings/hp_settings/equipment_stats_ui.bin'), 'Sheet')
        self.assertEqual(dv.errors, [])

    def test_pending_dropped_on_next_node(self):
        dv = self.dv
        calls = []
        original = dv.tab_adf.vnode_process
        dv.tab_adf.vnode_process = lambda vfs, vnode: (calls.append(vnode.uid), original(vfs, vnode))
        self.show('settings/hp_settings/equipment_stats_ui.bin')
        self.show('settings/hp_settings/equipment_data.bin')
        dv.tab_widget.setCurrentIndex(dv.tab_adf_index)       # desactive : ne doit rien calculer
        APP.processEvents()
        self.assertEqual(calls, [])

    def test_unreadable_sarc(self):
        dv = self.dv
        vnode = self.window.vfs.node_where_uid(self.uid(SARC_243_SP))
        import deca_gui.viewer_sarc as VS
        saved = VS.node_bytes
        VS.node_bytes = lambda vfs, n: b'SARC' + b'\0' * 4
        try:
            dv.tab_sarc.vnode_process(self.window.vfs, vnode)
        finally:
            VS.node_bytes = saved
        self.assertEqual(dv.tab_sarc.table.rowCount(), 0)
        self.assertTrue(dv.tab_sarc.summary.text().startswith('SARC not readable: '))

    def test_unreadable_rtpc(self):
        """Fichier RTPC tronque : une ligne d'erreur, lue apres coup (chargement paresseux)."""
        dv = self.dv
        vnode = self.window.vfs.node_where_uid(self.uid('settings/hp_settings/equipment_data.bin'))
        import deca_gui.viewer_rtpc as VR
        saved = VR.node_bytes
        VR.node_bytes = lambda vfs, n: b'RTPC\x01\x00\x00\x00'
        try:
            dv.tab_rtpc.vnode_process(self.window.vfs, vnode)
        finally:
            VR.node_bytes = saved
        top = dv.tab_rtpc.tree.model.root.children()
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0].cols[0], 'RTPC not readable')
        self.assertTrue(top[0].cols[1])


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    if '--spawn' in sys.argv:              # comme sous Windows : les processus du scan reimportent ce module
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
