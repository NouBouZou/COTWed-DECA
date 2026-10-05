#!/usr/bin/env python3
"""
Robustesse du scan DECA (anomalies A2, A3, A4 du journal de recherche) et noms COTW charges au scan.

  A2  un fichier illisible n'arrete plus le scan : il est signale ("loop_over_uid_wrapper: failed for", ligne
      comptee par deca_audit.py), ce qu'il avait mis en attente est annule, et les autres fichiers sont traites ;
  A3  ghidra_strings et field_strings sont lus meme si DECA n'est pas lance depuis la racine du depot ;
  A4  une entree TAB de taille 0 est dans la base (sans donnees, type symlink) et recoit son nom par son hash ;
  noms : cotw/dictionnaires/hp_noms.tsv est un dictionnaire du scan (cash_price, compatibility_N...).

Chaque scenario lance un vrai scan d'un faux jeu (faux_projet.py).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_scan_robustesse.py [--spawn]
"""
import contextlib
import io
import multiprocessing
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (niveau module : les processus spawn reimportent ce fichier)
import faux_projet  # noqa: E402
import sarc as S  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, k_type_str  # noqa: E402
from deca.util import deca_root  # noqa: E402

REAL_PATH = None   # un vrai chemin COTW du dictionnaire de DECA, pour l'entree vide


def real_path() -> str:
    global REAL_PATH
    if REAL_PATH is None:
        with open(os.path.join(deca_root(), 'resources', 'deca', 'hp', 'filenames.txt'), encoding='utf-8') as f:
            REAL_PATH = next(l.strip() for l in f if l.strip().endswith('.ddsc'))
    return REAL_PATH


def rtpc(name: str) -> bytes:
    r = Rtpc()
    r.version = 1
    r.root_node = W.make_node(0, [], [W.make_node(name, [W.make_prop('name', k_type_str, name.encode())])])
    return W.rtpc_to_binary(r)


def truncated_sarc() -> bytes:
    """En-tete SARC valide (le type est reconnu), repertoire coupe : la lecture des entrees echoue."""
    data, _ = S.build(S.Sarc(2, [S.Entry('a/b/c.epe', rtpc('c'), 0), S.Entry('a/b/d.epe', rtpc('d'), 0)]))
    return data[:24]


class Scan:
    """Un faux jeu scanne une fois pour la classe ; journal et base gardes pour les tests."""

    def __init__(self, files, cwd=None):
        self.root = Path(tempfile.mkdtemp(prefix='cotwed_robuste_'))
        project_file = faux_projet.build_fake_game(str(self.root), files)
        old = os.getcwd()
        self.error = None
        try:
            if cwd is not None:
                os.chdir(cwd)
            from deca.db_processor import vfs_structure_open
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    vfs = vfs_structure_open(project_file)
                    vfs.db_conn.close()
                except Exception as e:  # noqa: BLE001 - le test regarde quelle exception
                    self.error = e
        finally:
            os.chdir(old)
        self.work = self.root / 'work' / 'hp'
        self.log = (self.work / 'log.txt').read_text(encoding='utf-8', errors='replace')

    def db(self):
        return sqlite3.connect(str(self.work / 'db' / 'core.db'))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestFailingNode(unittest.TestCase):
    """A2 : un SARC tronque au milieu de fichiers sains."""

    @classmethod
    def setUpClass(cls):
        cls.scan = Scan([('settings/ok_avant.bin', rtpc('avant')), ('editor/casse.ee', truncated_sarc()),
                         ('settings/ok_apres.bin', rtpc('apres'))])

    @classmethod
    def tearDownClass(cls):
        cls.scan.cleanup()

    def test_scan_completes(self):
        self.assertIsNone(self.scan.error)
        self.assertIn('PROCESSING: COMPLETE', self.scan.log)
        self.assertNotIn('PROCESSING FAILED', self.scan.log)
        db = self.scan.db()
        try:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
        finally:
            db.close()

    def node(self, path):
        """(node_id, flags, file_type) du fichier : les faux chemins ne sont dans aucun dictionnaire, d'ou le hash."""
        from deca.hashes import hash32_func
        db = self.scan.db()
        try:
            return db.execute('SELECT node_id, flags, file_type FROM core_nodes WHERE v_hash = ?',
                              (int(hash32_func(path.encode())),)).fetchone()
        finally:
            db.close()

    def test_failure_reported_once_with_its_cause(self):
        failed = [l for l in self.scan.log.splitlines() if 'loop_over_uid_wrapper: failed for:' in l]
        self.assertEqual(len(failed), 1, failed)       # une ligne par noeud : deca_audit compte ces lignes
        self.assertIn('id: {},'.format(self.node('editor/casse.ee')[0]), failed[0])
        self.assertIn('-- ', failed[0])                  # cause et endroit
        self.assertIn('loop_over_uid_wrapper: ancestor of', self.scan.log)

    def test_other_files_processed(self):
        from deca.db_types import node_flag_processed_file_type, node_flag_processing_failed
        for path in ('settings/ok_avant.bin', 'settings/ok_apres.bin'):
            _uid, flags, ftype = self.node(path)
            self.assertEqual(ftype, 'rtpc')
            self.assertTrue(flags & node_flag_processed_file_type, path)
        uid, flags, ftype = self.node('editor/casse.ee')
        self.assertEqual(ftype, 'sarc')
        self.assertFalse(flags & node_flag_processed_file_type)    # reste non traite
        self.assertTrue(flags & node_flag_processing_failed)       # et marque : pas de nouvel essai
        db = self.scan.db()
        try:
            children = db.execute('SELECT COUNT(*) FROM core_nodes WHERE parent_id = ?', (uid,)).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(children, 0)                    # rien de ce qu'il avait commence n'est ecrit


class TestRollback(unittest.TestCase):
    def test_mark_and_rollback(self):
        from deca.db_wrap import DbWrap
        w = DbWrap.__new__(DbWrap)
        for name in DbWrap._BUFFERS:
            setattr(w, name, [])
        w.node_add('a')
        w.node_update('a')
        mark = w.mark()
        w.node_add('b')
        w.node_update('b')
        w._objects.append('o')
        w.rollback(mark)
        self.assertEqual((w._nodes_to_add, w._nodes_to_update, w._objects), (['a'], ['a'], []))


class TestResourcesAndNames(unittest.TestCase):
    """A3 (scan lance depuis un autre dossier), A4 (entree vide) et noms COTW."""

    @classmethod
    def setUpClass(cls):
        cls.elsewhere = tempfile.mkdtemp(prefix='cotwed_ailleurs_')
        cls.scan = Scan([('settings/ok.bin', rtpc('ok')), (real_path(), b'')], cwd=cls.elsewhere)

    @classmethod
    def tearDownClass(cls):
        cls.scan.cleanup()
        shutil.rmtree(cls.elsewhere, ignore_errors=True)

    def test_dictionaries_read_from_any_folder(self):
        self.assertIsNone(self.scan.error)
        for name in ('ghidra_strings/hp.txt', 'field_strings/cross_game.txt', 'cotw/dictionnaires/hp_noms.tsv'):
            self.assertRegex(self.scan.log, r'STRINGS FROM RESOURCES: .*{}: Loaded \d+ strings'.format(name))

    def test_cotw_names_in_core_strings(self):
        db = self.scan.db()
        try:
            found = {r[0] for r in db.execute(
                "SELECT CAST(string AS TEXT) FROM core_strings WHERE CAST(string AS TEXT) IN "
                "('cash_price', 'compatibilities', 'compatibility_7', 'secondary_ids', 'display_description')")}
        finally:
            db.close()
        self.assertEqual(found, {'cash_price', 'compatibilities', 'compatibility_7', 'secondary_ids',
                                 'display_description'})

    def test_zero_length_tab_entry_is_named(self):
        from deca.hashes import hash32_func
        db = self.scan.db()
        try:
            row = db.execute('SELECT CAST(v_path AS TEXT), file_type, parent_offset, size_u FROM core_nodes '
                             'WHERE v_hash = ?', (int(hash32_func(real_path().encode())),)).fetchone()
        finally:
            db.close()
        self.assertIsNotNone(row, 'entree de taille 0 absente de la base')
        self.assertEqual(row, (real_path(), 'symlink', None, 0))


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
