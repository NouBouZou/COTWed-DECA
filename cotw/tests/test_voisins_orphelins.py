"""Tests de cotw/voisins_orphelins.py sur un faux projet dont on efface quelques noms."""
import csv
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / 'python' / 'deca'))

import faux_projet  # noqa: E402
import voisins_orphelins as V  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402

FILES = [('ui/a/x_01.bin', b'A' * 40), ('ui/a/x_02.bin', b'B' * 40), ('ui/a/x_03.bin', b'C' * 40),
         ('ui/a/x_04.bin', b'D' * 40), ('ui/b/y.bin', b'E' * 40), ('ui/b/z.bin', b'F' * 40)]
EFFACES = ['ui/a/x_02.bin', 'ui/a/x_03.bin', 'ui/b/z.bin']


def h(path):
    return int(hash32_func(path.encode('utf-8')))


class TestVoisins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.project = faux_projet.build_fake_project(cls.tmp.name, FILES)
        db = sqlite3.connect(os.path.join(cls.project, 'db', 'core.db'))
        for p in EFFACES:
            db.execute('UPDATE core_nodes SET v_path = NULL WHERE v_hash = ?', (h(p),))
        db.commit()
        db.close()
        cls.out = Path(cls.tmp.name) / 'sortie'
        cls.rc = V.main([cls.project, '--sortie', str(cls.out)])
        with open(cls.out / 'voisins_orphelins.csv', newline='', encoding='utf-8-sig') as f:
            cls.rows = {r['hash']: r for r in csv.DictReader(f, delimiter=';')}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, path):
        return next(r for k, r in self.rows.items() if k.endswith('{:08x}'.format(h(path))))

    def test_only_unnamed_listed(self):
        self.assertEqual(self.rc, 0)
        self.assertEqual(len(self.rows), len(EFFACES))

    def test_neighbours_in_archive_order(self):
        r = self.row('ui/a/x_02.bin')
        self.assertEqual(r['nommes_avant'].split(' | ')[0], 'ui/a/x_01.bin')
        self.assertEqual(r['nommes_apres'].split(' | ')[0], 'ui/a/x_04.bin')
        self.assertEqual(r['sans_nom_depuis_le_precedent'], '0')
        r = self.row('ui/a/x_03.bin')
        self.assertEqual(r['nommes_avant'].split(' | ')[0], 'ui/a/x_01.bin')
        self.assertEqual(r['sans_nom_depuis_le_precedent'], '1')
        r = self.row('ui/b/z.bin')
        self.assertEqual(r['nommes_avant'].split(' | ')[0], 'ui/b/y.bin')
        self.assertEqual(r['nommes_apres'], '')

    def test_report_runs_and_dirs(self):
        md = (self.out / 'voisins_orphelins.md').read_text(encoding='utf-8')
        self.assertIn('`ui/a`', md)
        self.assertIn('2 : 1', md)  # x_02 + x_03 : une suite de 2
        self.assertIn('Archives triees par chemin', md)
        self.assertIn('**1 / 1**', md)

    def test_readonly(self):
        db = sqlite3.connect(os.path.join(self.project, 'db', 'core.db'))
        n = db.execute('SELECT COUNT(*) FROM core_nodes WHERE v_path IS NULL AND v_hash IN (?, ?, ?)',
                       tuple(h(p) for p in EFFACES)).fetchone()[0]
        db.close()
        self.assertEqual(n, len(EFFACES))


if __name__ == '__main__':
    unittest.main()
