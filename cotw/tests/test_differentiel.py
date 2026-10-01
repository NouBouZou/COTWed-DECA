#!/usr/bin/env python3
"""
Tests de cotw/differentiel.py : probabilites, filtrage du bruit, puis differentiel complet sur le faux jeu de
scenario_registre.py (le registre cache doit sortir en tete, equipment_data et les spawn defs non).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_differentiel.py
"""
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import differentiel as DF  # noqa: E402
import indexer_refs as I  # noqa: E402
import scenario_registre as S  # noqa: E402


class TestProbabilities(unittest.TestCase):
    def test_poisson(self):
        self.assertAlmostEqual(DF.poisson_sf_log10(1, 1e-3), math.log10(1 - math.exp(-1e-3)), places=6)
        self.assertAlmostEqual(10 ** DF.poisson_sf_log10(2, 1.0), 1 - 2 / math.e, places=6)
        self.assertEqual(DF.poisson_sf_log10(0, 5.0), 0.0)
        self.assertLess(DF.poisson_sf_log10(100, 0.01), -200)

    def test_exclusion(self):
        self.assertAlmostEqual(DF.exclusion_log10(113, 5, 113), -math.log10(math.comb(118, 5)), places=6)
        self.assertAlmostEqual(10 ** DF.exclusion_log10(3, 2, 2), 3 / 10, places=9)


def synthetic_index(path: Path):
    """Index minimal : un fichier binaire enorme avec 3 coincidences de hash, un fichier texte avec 1 citation."""
    db = sqlite3.connect(str(path))
    db.executescript(I.SCHEMA)
    db.executemany('INSERT INTO identifiers VALUES (?,?,?,?,?)', [
        (1, 'a1', 'ammo', 'nom', 'a1'), (2, 'a2', 'ammo', 'nom', 'a2'), (3, 'u1', 'ammo', 'nom', 'u1')])
    db.executemany('INSERT INTO meta VALUES (?,?)', [('motifs', json.dumps({'texte': 3, 'hash32': 3, 'hash48': 3, 'hash64': 3})),
                                                     ('created', 'test'), ('options', '{}')])
    db.execute("INSERT INTO files VALUES (1, 'node:1', 1, 'gros.bin', NULL, 1, 'brut', 'ok', NULL, ?, 0, 0)", (4 * 10 ** 9,))
    db.execute("INSERT INTO files VALUES (2, 'node:2', 2, 'liste.txt', 'txt', 1, 'brut', 'ok', NULL, 10, 0, 0)")
    db.executemany('INSERT INTO hits VALUES (?,?,?,?,?,?)', [
        (1, 1, 'hash32', 1, '[0]', '[]'), (1, 2, 'hash32', 1, '[4]', '[]'), (1, 3, 'hash32', 1, '[8]', '[]'),
        (2, 1, 'texte', 1, '[0]', '[]')])
    db.commit()
    db.close()


class TestNoise(unittest.TestCase):
    def test_random_hash_hits_in_big_file_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'i.db'
            synthetic_index(path)
            text = '\n'.join(DF.differential(DF.Index(path), ['a1', 'a2'], ['u1'], None, -3, 10))
            self.assertIn('`liste.txt`', text)
            self.assertNotIn('`gros.bin`', text)   # 3 coincidences pour ~3 attendues : du bruit
            self.assertIn('écartés : 1', text)


class TestScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_diff_')
        cls.project = S.build(cls.tmp.name)
        cls.index = Path(cls.tmp.name) / 'refs.db'
        I.main([cls.project, '--index', str(cls.index), '--processus', '1'])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def report(self, *extra):
        out = Path(self.tmp.name) / 'diff.md'
        DF.main([self.project, '--index', str(self.index), '--absents', ','.join(S.ABSENT), '--out', str(out)] + list(extra))
        return out.read_text(encoding='utf-8')

    def test_registry_ranked_first_and_equipment_data_not_candidate(self):
        text = self.report()
        part1, part2 = text.split('## 2.')[0], text.split('## 2.')[1].split('## 3.')[0]
        rows = [l for l in part1.splitlines() if l.startswith('| 1 ') or l.startswith('| 2 ') or l.startswith('| 3 ')]
        top2 = ' '.join(rows[:2])
        self.assertIn(S.REGISTRY, top2)
        self.assertIn(S.UNKNOWN, top2)
        self.assertIn('[exe]', rows[2])
        self.assertIn('3/3 (100%)', top2)
        for f in ('equipment_data.bin', 'spawn_ammunition_defs.bin', 'equipment_stats_ui.bin'):
            self.assertNotIn(f, part1)
            self.assertIn(f, part2)

    def test_presents_from_file(self):
        names = Path(self.tmp.name) / 'vus.txt'
        names.write_text('\n'.join(S.AVAILABLE[:2]) + '\n', encoding='utf-8')
        text = self.report('--presents-fichier', str(names))
        self.assertIn('Présents : 2 objets', text)

    def test_checklist(self):
        path = Path(self.tmp.name) / 'liste.csv'
        rows = ['categorie;nom;visible'] + ['ammo;{};x'.format(n) for n in S.AVAILABLE] + ['ammo;{};'.format(n) for n in S.ABSENT]
        path.write_text('\n'.join(rows) + '\n', encoding='utf-8-sig')
        out = Path(self.tmp.name) / 'diff_liste.md'
        DF.main([self.project, '--index', str(self.index), '--liste', str(path), '--out', str(out)])
        text = out.read_text(encoding='utf-8')
        self.assertIn('Présents : 3 objets', text)
        self.assertIn(S.REGISTRY, text.split('## 2.')[0])

    def test_object_report(self):
        out = Path(self.tmp.name) / 'objet.md'
        DF.main([self.project, '--index', str(self.index), '--objet', S.AVAILABLE[0], '--out', str(out)])
        text = out.read_text(encoding='utf-8')
        for f in (S.REGISTRY, S.UNKNOWN, '[exe]', 'equipment_data.bin', 'spawn_ammunition_defs.bin'):
            self.assertIn(f, text)
        self.assertIn('hash64', text)
        self.assertIn('valeur u32', text)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
