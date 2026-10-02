#!/usr/bin/env python3
"""
Tests de l'interface DECA (deca_gui) et des fonctions du coeur qu'elle utilise, hors ecran (Qt "offscreen").

Chaque test de fenetre part du projet de demonstration (cotw/demo_projet.py), construit dans un dossier temporaire
par un vrai scan DECA : rien n'est ecrit dans work\\ ni dans work_demo\\.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_gui.py
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows)
import demo_projet  # noqa: E402
import faux_projet  # noqa: E402

from PySide6.QtCore import QModelIndex, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from deca.db_types import regexp, regexp_match  # noqa: E402
from deca.db_view import regex_literal_prefix, mask_is_match_all  # noqa: E402

APP = None


def setUpModule():
    # Cree ici et pas a l'import : sous Windows, chaque processus du scan DECA (multiprocessing "spawn") reimporte ce
    # module et ne doit pas ouvrir sa propre application Qt.
    global APP
    APP = QApplication.instance() or QApplication([])

# Chemins ajoutes a la base du projet demo pour les tests de filtre : TEXT et BLOB melanges, accents, '_' et '%'
EXTRA_PATHS = [
    ('textures/été/neige_01.ddsc', 'text'),
    ('textures/ete/neige_02.ddsc', 'blob'),
    ('Settings/majuscules.bin', 'text'),
    ('settings/50%_off.bin', 'blob'),
    ('settingsXhp/like_trap.bin', 'text'),
]


def wait_until(predicate, timeout=120.0):
    """Traite les evenements Qt jusqu'a ce que predicate() soit vrai ; False si le delai expire."""
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    APP.processEvents()
    return predicate()


def add_extra_paths(project_dir: Path):
    db = sqlite3.connect(str(project_dir / 'db' / 'core.db'))
    next_id = db.execute('SELECT MAX(node_id) FROM core_nodes').fetchone()[0] + 1
    for i, (path, kind) in enumerate(EXTRA_PATHS):
        value = path if kind == 'text' else path.encode('utf-8')
        db.execute('INSERT INTO core_nodes (node_id, flags, parent_id, parent_index, parent_offset, v_hash, v_path, '
                   'file_type, size_c, size_u) VALUES (?, ?, 3, ?, 0, ?, ?, NULL, 0, 0)',
                   (next_id + i, 1 << 16, 1000 + i, 0x10000000 + i, value))
    db.commit()
    db.close()


def reference_view(db_file: Path, mask: str, parent_id=None):
    """Semantique de VfsView avant optimisation : tout lire, puis re.match du masque en Python."""
    db = sqlite3.connect(str(db_file))
    rows = db.execute('SELECT node_id, v_path, p_path, file_type, parent_offset, parent_id FROM core_nodes').fetchall()
    db.close()
    expr = re.compile(mask)
    nodes_map, uids, no_vpath = {}, set(), set()
    for uid, v_path, p_path, file_type, parent_offset, pid in rows:
        if parent_id is not None and pid != parent_id:
            continue
        uids.add(uid)
        if v_path is None:
            if p_path is None:
                no_vpath.add(uid)
            continue
        v_path = v_path.decode('utf-8') if isinstance(v_path, bytes) else v_path
        if expr.match(v_path) is None:
            continue
        lst = nodes_map.setdefault(v_path, [[], []])
        lst[0 if (file_type != 'symlink' and parent_offset is not None) else 1].append(uid)
    return nodes_map, uids, no_vpath


class DemoProject:
    """Projet demo construit une fois pour le module (un scan reel prend quelques secondes)."""
    root = None
    project_file = None

    @classmethod
    def get(cls) -> Path:
        if cls.project_file is None:
            cls.root = Path(tempfile.mkdtemp(prefix='cotwed_gui_'))
            cls.project_file = demo_projet.build(cls.root / 'demo')
            add_extra_paths(cls.project_file.parent)
        return cls.project_file

    @classmethod
    def cleanup(cls):
        if cls.root is not None:
            shutil.rmtree(cls.root, ignore_errors=True)


def tearDownModule():
    DemoProject.cleanup()


class TestRegexHelpers(unittest.TestCase):
    def test_regexp_text_bytes_unicode(self):
        self.assertTrue(regexp('été', 'textures/été/a'))
        self.assertTrue(regexp(b'\xc3\xa9t\xc3\xa9', 'textures/été/a'))
        self.assertTrue(regexp('été', 'textures/été/a'.encode('utf-8')))
        self.assertFalse(regexp('^été', 'textures/été/a'))   # search, mais l'ancre reste respectee
        self.assertTrue(regexp_match('^textures/', b'textures/x'))
        self.assertFalse(regexp_match('été', 'textures/été'))  # match = debut de chaine
        self.assertFalse(regexp('x', None))

    def test_literal_prefix(self):
        cases = {
            '^settings/.*$': 'settings/',
            '^settings/hp_settings/equipment_data\\.bin$': 'settings/hp_settings/equipment_data.bin',
            '^abc?d$': 'ab', '^ab{2}$': 'a', '^ab\\d+$': 'ab', 'settings/.*': 'settings/',
            '^a|b$': '', '(?i)^set.*$': '', '^settings/(a|b).*$': '', '^.*equipment.*$': '', '^[st]e$': '',
            '^textures/é.*$': 'textures/é',
        }
        for mask, expected in cases.items():
            with self.subTest(mask=mask):
                self.assertEqual(regex_literal_prefix(mask), expected)

    def test_match_all(self):
        self.assertTrue(mask_is_match_all(b'^.*$'))
        self.assertTrue(mask_is_match_all(None))
        self.assertFalse(mask_is_match_all('^settings/.*$'))


class TestViewEquivalence(unittest.TestCase):
    """VfsView (masque applique par SQLite) donne exactement le resultat de l'ancien algorithme."""

    @classmethod
    def setUpClass(cls):
        from deca.db_processor import vfs_structure_open
        cls.project_file = DemoProject.get()
        cls.db_file = cls.project_file.parent / 'db' / 'core.db'
        cls.vfs = vfs_structure_open(str(cls.project_file))

    @classmethod
    def tearDownClass(cls):
        cls.vfs.db_conn.close()

    def check(self, mask, parent_id=None):
        from deca.db_view import VfsView
        if parent_id is None:
            view = VfsView(self.vfs, None, mask.encode('utf-8'))
        else:
            root = VfsView(self.vfs, None, mask.encode('utf-8'))
            view = VfsView(root, parent_id=parent_id)
        expected_map, expected_uids, expected_no = reference_view(self.db_file, mask, parent_id)
        got_map = {k: [sorted(v[0]), sorted(v[1])] for k, v in view.nodes_visible_map_get().items()}
        exp_map = {k: [sorted(v[0]), sorted(v[1])] for k, v in expected_map.items()}
        self.assertEqual(got_map, exp_map, mask)
        self.assertEqual(view.nodes_visible_uids_get(), expected_uids)
        self.assertEqual(view.nodes_visible_uids_no_vpath_get(), expected_no)
        return got_map

    def test_masks(self):
        for mask in ['^.*$', '^settings/.*$', '^settings/hp_settings/equipment_data\\.bin$', '^.*\\.ee$',
                     '^textures/été/.*$', '^textures/[eé]te/.*$', '^settings/50%.*$', '^settings_.*$',
                     '^Settings/.*$', '^.*neige_0[12]\\.ddsc$', '(?i)^settings/.*$']:
            with self.subTest(mask=mask):
                self.check(mask)

    def test_like_prefix_does_not_change_case_or_wildcards(self):
        # LIKE ignore la casse et '_' est un joker : la regex doit rester seule juge
        got = self.check('^settings/.*$')
        self.assertNotIn('Settings/majuscules.bin', got)
        self.assertNotIn('settingsXhp/like_trap.bin', got)
        self.assertIn('settings/50%_off.bin', got)

    def test_child_view(self):
        self.check('^.*$', parent_id=8)      # contenu d'un SARC
        self.check('^.*\\.epe$', parent_id=8)

    def test_mask_change_keeps_uid_sets(self):
        from deca.db_view import VfsView
        view = VfsView(self.vfs, None, b'^.*$')
        before = view.nodes_visible_uids_get()
        view.mask_set(b'^settings/.*$')
        self.assertIs(view.nodes_visible_uids_get(), before)  # meme objet : les listes ne sont pas reconstruites

    def test_sorted_uids(self):
        from deca.db_view import VfsView
        view = VfsView(self.vfs, None, b'^.*$')
        by_size = view.nodes_visible_uids_sorted('size_u', descending=True)
        self.assertEqual(set(by_size), view.nodes_visible_uids_get())
        sizes = dict(self.vfs.db_query_all('SELECT node_id, size_u FROM core_nodes'))
        values = [sizes[u] if sizes[u] is not None else -1 for u in by_size]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_parent_id_index(self):
        names = {r[1] for r in self.vfs.db_query_all("PRAGMA index_list('core_nodes')")}
        self.assertIn('index_core_nodes_parent_id', names)
        plan = ' '.join(str(r) for r in self.vfs.db_query_all(
            'EXPLAIN QUERY PLAN SELECT node_id FROM core_nodes WHERE parent_id == 8'))
        self.assertIn('index_core_nodes_parent_id', plan)


class GuiTestCase(unittest.TestCase):
    def setUp(self):
        from deca_gui.main import MainWindow
        self.errors = []
        self.window = MainWindow()
        self.window.error_dialog = self.errors.append   # pas de boite modale pendant les tests
        self.window.resize(1400, 850)
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        wait_until(lambda: not self.window.task_running(), 120)
        if self.window.vfs is not None:
            self.window.tabs_nodes_clear()
            self.window.vfs.db_conn.close()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def open_demo(self):
        self.window.project_open_path(str(DemoProject.get()))
        self.assertTrue(self.window.task_running())
        self.assertTrue(wait_until(lambda: not self.window.task_running() and self.window.vfs is not None, 120),
                        'ouverture non terminee : {}'.format(self.errors))
        APP.processEvents()

    def tree_find(self, name):
        dirw = self.window.ui.tabs_nodes.widget(0)
        model = dirw.source_model

        def walk(index):
            for r in range(model.rowCount(index)):
                c = model.index(r, 0, index)
                if c.internalPointer().name == name and not hasattr(c.internalPointer(), 'children'):
                    return c
                found = walk(c)
                if found is not None:
                    return found
            return None

        return dirw, walk(model.index(0, 0, QModelIndex()))


class TestWindow(GuiTestCase):
    def test_open_in_background_and_views(self):
        self.open_demo()
        tabs = self.window.ui.tabs_nodes
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ['Directory', 'Non-Mapped List', 'Raw List'])
        self.assertIn('Opening project', self.window.log_view.toPlainText())
        self.assertIn('PROCESSING: COMPLETE', self.window.log_view.toPlainText())
        self.assertEqual(self.errors, [])

        dirw, idx = self.tree_find('equipment_data.bin')
        self.assertIsNotNone(idx)
        dirw.double_clicked(idx)
        APP.processEvents()
        dv = self.window.ui.data_view
        self.assertEqual(dv.tab_widget.tabText(dv.tab_widget.currentIndex()), 'RTPC')
        info = dv.tab_info.text_box.toPlainText()
        self.assertIn('settings/hp_settings/equipment_data.bin', info)
        self.assertIn('Ancestors', info)
        self.assertIn('game0.tab', info)          # parent : l'archive TAB
        self.assertIn('Children: 0', info)

    def test_info_tab_for_sarc_and_orphan(self):
        self.open_demo()
        dv = self.window.ui.data_view
        self.window.vnode_2click_selected([8])      # .ee (SARC) de la .243 SP
        self.assertIn('Children: 2', dv.tab_info.text_box.toPlainText())
        db_file = DemoProject.get().parent / 'db' / 'core.db'
        db = sqlite3.connect(str(db_file))
        orphan = db.execute('SELECT node_id FROM core_nodes WHERE v_path IS NULL AND p_path IS NULL').fetchone()[0]
        db.close()
        self.window.vnode_2click_selected([orphan])
        self.assertIn('(no name)', dv.tab_info.text_box.toPlainText())

    def test_filter(self):
        self.open_demo()
        dirw = self.window.ui.tabs_nodes.widget(0)
        self.window.ui.filter_edit.setText('settings/.*')
        self.window.filter_text_accepted(True)
        APP.processEvents()
        visible = set(dirw.source_model.vfs_view.nodes_visible_map_get())
        self.assertIn('settings/hp_settings/equipment_data.bin', visible)
        self.assertTrue(all(p.startswith('settings/') for p in visible))
        _, idx = self.tree_find('equipment_data.bin')
        self.assertIsNotNone(idx)
        _, idx = self.tree_find('equipment_ammo_243_sp_01_alpha.dds')
        self.assertIsNone(idx)

    def test_non_ascii_filter_does_not_crash(self):
        self.open_demo()
        self.window.ui.filter_edit.setText('textures/été/.*')
        self.window.filter_text_accepted(True)
        APP.processEvents()
        visible = set(self.window.ui.tabs_nodes.widget(0).source_model.vfs_view.nodes_visible_map_get())
        self.assertEqual(visible, {'textures/été/neige_01.ddsc'})

    def test_non_mapped_and_sort(self):
        self.open_demo()
        non_mapped = self.window.ui.tabs_nodes.widget(1).model
        self.assertEqual(non_mapped.rowCount(), 3)   # les 3 fichiers dont le nom a ete efface
        raw = self.window.ui.tabs_nodes.widget(2)
        raw.table_view.sortByColumn(6, Qt.DescendingOrder)   # Size_U decroissant
        APP.processEvents()
        sizes = [raw.model.data(raw.model.index(r, 6)) for r in range(raw.model.rowCount())]
        numeric = [int(s) if s not in ('None', '') else -1 for s in sizes]
        self.assertEqual(numeric, sorted(numeric, reverse=True))
        raw.table_view.sortByColumn(8, Qt.AscendingOrder)    # chemin
        APP.processEvents()
        self.assertEqual(raw.model.rowCount(), len(raw.model.vfs_view.nodes_visible_uids_get()))

    def test_reopen_replaces_tabs_and_close_archive_tab(self):
        self.open_demo()
        first_vfs = self.window.vfs
        self.open_demo()
        self.assertEqual(self.window.ui.tabs_nodes.count(), 3)
        with self.assertRaises(sqlite3.ProgrammingError):   # base du premier projet fermee
            first_vfs.db_query_one('SELECT 1')
        dirw, idx = self.tree_find('equipment_ammo_243_sp_01.ee')
        dirw.double_clicked(idx)
        APP.processEvents()
        self.window.ui.data_view.tab_sarc.open_archive()
        APP.processEvents()
        tabs = self.window.ui.tabs_nodes
        self.assertEqual(tabs.count(), 4)
        tabs.setCurrentIndex(3)
        archive_view = tabs.widget(3).vfs_view_get()
        self.assertEqual(len(archive_view.nodes_visible_map_get()), 2)   # .epe et .fmod_guids
        self.window.slot_nodes_tab_close(3)
        APP.processEvents()
        self.assertEqual(tabs.count(), 3)
        self.window.tabs_nodes_clear()                 # plus aucun onglet : ne doit pas planter
        APP.processEvents()
        self.assertIsNone(self.window.vfs_view_current())

    def test_switch_project_clears_data_view(self):
        """Le panneau de droite oublie le noeud de l'ancien projet : Open Archive ne doit rien ouvrir."""
        self.open_demo()
        dv = self.window.ui.data_view
        self.window.vnode_2click_selected([8])      # .ee (SARC)
        self.assertIsNotNone(dv.tab_sarc.vnode)
        self.open_demo()
        self.assertIsNone(dv.tab_sarc.vnode)
        self.assertFalse(dv.tab_widget.isEnabled())
        self.assertEqual(dv.tab_info.text_box.toPlainText(), '')
        dv.tab_sarc.open_archive()
        APP.processEvents()
        self.assertEqual(self.window.ui.tabs_nodes.count(), 3)  # aucun onglet d'archive ajoute

    def test_scan_state_and_resume_label(self):
        from deca_gui.project_task import project_scan_state, SCAN_COMPLETE, SCAN_STARTED
        project_file = DemoProject.get()
        self.assertEqual(project_scan_state(str(project_file)), SCAN_COMPLETE)
        self.assertIsNone(project_scan_state(str(project_file.parent / 'absent' / 'project.json')))
        root = Path(tempfile.mkdtemp(prefix='cotwed_state_'))
        try:
            copy = root / 'hp'
            shutil.copytree(project_file.parent, copy)
            db = sqlite3.connect(str(copy / 'db' / 'core.db'))
            db.execute('PRAGMA user_version = 1')
            db.commit()
            db.close()
            digest = (copy / 'db' / 'core.db').read_bytes()
            self.assertEqual(project_scan_state(str(copy / 'project.json')), SCAN_STARTED)
            self.assertEqual((copy / 'db' / 'core.db').read_bytes(), digest)   # lecture seule
            self.window.project_open_path(str(copy))
            self.assertEqual(self.window._task_label, 'Resuming unfinished scan')
            self.assertTrue(wait_until(lambda: not self.window.task_running(), 300))
            self.assertIn('The scan of this project was interrupted', self.window.log_view.toPlainText())
            self.assertEqual(self.window.vfs.db_query_one('PRAGMA user_version')[0], 2)
            self.window.project_close()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_project(self):
        self.window.project_open_path(str(DemoProject.get().parent / 'absent' / 'project.json'))
        APP.processEvents()
        self.assertFalse(self.window.task_running())
        self.assertTrue(self.errors and 'not found' in self.errors[0])


class TestBackgroundScan(GuiTestCase):
    def test_scan_keeps_window_responsive(self):
        """Ouvrir un projet jamais scanne lance le scan complet dans le fil de travail ; la fenetre reste vivante."""
        root = Path(tempfile.mkdtemp(prefix='cotwed_scan_'))
        try:
            project_file = faux_projet.build_fake_game(str(root), demo_projet.files())
            ticks = []
            timer = QTimer()
            timer.setInterval(20)
            timer.timeout.connect(lambda: ticks.append(self.window.task_running()))
            timer.start()
            self.window.project_open_path(project_file)
            self.assertTrue(wait_until(lambda: not self.window.task_running(), 300), 'scan non termine')
            timer.stop()
            self.assertEqual(self.errors, [])
            self.assertGreater(sum(ticks), 5, 'la boucle d\'evenements n\'a pas tourne pendant le scan')
            version = self.window.vfs.db_query_one('PRAGMA user_version')[0]
            self.assertEqual(version, 2)
            self.assertIn('PROCESSING: COMPLETE', self.window.log_view.toPlainText())
            self.window.tabs_nodes_clear()
            self.window.vfs.db_conn.close()
            self.window.vfs = None
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_failure_is_reported(self):
        root = Path(tempfile.mkdtemp(prefix='cotwed_bad_'))
        try:
            bad = root / 'project.json'
            bad.write_text('{ pas du json', encoding='utf-8')
            self.window.project_open_path(str(bad))
            self.assertTrue(wait_until(lambda: not self.window.task_running(), 60))
            self.assertEqual(len(self.errors), 1)
            self.assertIn('failed', self.errors[0])
            self.assertIsNone(self.window.vfs)
        finally:
            shutil.rmtree(root, ignore_errors=True)


CLOSE_DURING_SCAN = r'''
import json, os, sys, time
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path[:0] = [{cotw!r}, {tests!r}]
import _projet  # noqa
import multiprocessing


def main():
    if {spawn!r}:
        multiprocessing.set_start_method('spawn', force=True)
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication([])
    import deca_gui.main as M
    M.QMessageBox.question = lambda *a, **k: QMessageBox.Yes
    w = M.MainWindow()
    w.show()
    w.project_open_path({project!r})
    end = time.time() + 300
    while not multiprocessing.active_children() and w.task_running() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    children = multiprocessing.active_children()
    def hard_exit(code):
        print(json.dumps({{'workers': len(children), 'alive_after': [c.is_alive() for c in children],
                          'task_running': w.task_running()}}), flush=True)
        os._exit(code)
    w.hard_exit = hard_exit
    w.close()
    print('NOT EXITED', flush=True)


if __name__ == '__main__':
    main()
'''


class TestCloseDuringScan(unittest.TestCase):
    """
    Fermer la fenetre pendant un scan : les processus du scan sont arretes et l'application se termine.

    Lance dans un processus Python a part, puisque l'application se termine reellement.
    """

    def run_case(self, spawn):
        root = Path(tempfile.mkdtemp(prefix='cotwed_close_'))
        try:
            project_file = faux_projet.build_fake_game(str(root), demo_projet.files())
            script = root / 'close_during_scan.py'
            script.write_text(CLOSE_DURING_SCAN.format(
                cotw=str(HERE.parent), tests=str(HERE), project=project_file, spawn=spawn), encoding='utf-8')
            import subprocess
            t0 = time.time()
            proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300)
            lines = [l for l in proc.stdout.splitlines() if l.startswith('{') or l.startswith('NOT EXITED')]
            self.assertEqual(proc.returncode, 1, proc.stderr[-2000:])
            self.assertEqual(len(lines), 1, proc.stdout[-2000:])
            import json
            result = json.loads(lines[0])
            self.assertGreater(result['workers'], 0, 'aucun processus de scan au moment de la fermeture')
            self.assertEqual(result['alive_after'], [False] * result['workers'])
            self.assertTrue(result['task_running'])
            self.assertLess(time.time() - t0, 120)
            from deca_gui.project_task import project_scan_state, SCAN_STARTED
            self.assertEqual(project_scan_state(project_file), SCAN_STARTED)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_close_during_scan(self):
        self.run_case(spawn=sys.platform.startswith('win'))

    @unittest.skipIf(sys.platform.startswith('win'), 'deja couvert : spawn est le mode par defaut sous Windows')
    def test_close_during_scan_spawn(self):
        self.run_case(spawn=True)


class TestNewProjectFlow(GuiTestCase):
    """
    Choix proposes par File > New Project. Le scan lui-meme n'est jamais lance ici : vfs_structure_new ecrit dans
    <depot>\\..\\work\\hp, c'est-a-dire dans le vrai projet. task_start et le dossier du projet sont remplaces.
    """

    def setUp(self):
        super().setUp()
        import deca_gui.main as M
        self.M = M
        self.root = Path(tempfile.mkdtemp(prefix='cotwed_new_'))
        self.exe = str(Path(faux_projet.build_fake_game(str(self.root), [('a.txt', b'x')])).parents[2] /
                       'theHunterCotW' / 'theHunterCotW_F.exe')
        self.working_dir = str(self.root / 'work' / 'hp')
        self.started = []
        self._saved = (M.QFileDialog.getOpenFileName, M.project_working_dir, M.QMessageBox.warning)
        M.QFileDialog.getOpenFileName = lambda *a, **k: (self.exe, '')
        M.project_working_dir = lambda game_info: self.working_dir
        M.QMessageBox.warning = lambda *a, **k: M.QMessageBox.Yes
        self.window.task_start = lambda kind, path: self.started.append((kind, path))

    def tearDown(self):
        M = self.M
        M.QFileDialog.getOpenFileName, M.project_working_dir, M.QMessageBox.warning = self._saved
        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()

    def test_existing_project_open(self):
        self.window.existing_project_dialog = lambda wd: 'open'
        self.window.project_new(True)
        self.assertEqual(self.started, [('open', os.path.join(self.working_dir, 'project.json'))])

    def test_existing_project_cancel(self):
        self.window.existing_project_dialog = lambda wd: 'cancel'
        self.window.project_new(True)
        self.assertEqual(self.started, [])
        self.assertTrue(os.path.isfile(os.path.join(self.working_dir, 'project.json')))

    def test_existing_project_rescan_deletes_project_and_log(self):
        log_file = self.working_dir + 'log.txt'     # emplacement reel du journal DECA : work/hplog.txt
        Path(log_file).write_text('journal', encoding='utf-8')
        self.window.existing_project_dialog = lambda wd: 'rescan'
        self.window.project_new(True)
        self.assertFalse(os.path.exists(self.working_dir))
        self.assertFalse(os.path.exists(log_file))
        self.assertEqual(self.started, [('new', self.exe.replace('\\', '/'))])

    def test_no_existing_project(self):
        shutil.rmtree(self.working_dir)
        self.window.project_new(True)
        self.assertEqual(self.started, [('new', self.exe.replace('\\', '/'))])

    def test_unknown_game(self):
        other = self.root / 'OtherGame' / 'game.exe'
        other.parent.mkdir()
        other.write_bytes(b'MZ')
        self.M.QFileDialog.getOpenFileName = lambda *a, **k: (str(other), '')
        self.window.project_new(True)
        self.assertEqual(self.started, [])
        self.assertTrue(self.errors and 'Unknown game' in self.errors[0])


if __name__ == '__main__':
    unittest.main()
