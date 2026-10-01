#!/usr/bin/env python3
"""
Tests de cotw/adf_dump.py, de cotw/citations.py et de la section ADF de comparer_objets.py, sur un faux
projet DECA (vrai scan) contenant un ADF fabrique par adf_minimal.py.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_adf_dump.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import adf_dump as AD  # noqa: E402
import adf_minimal as AM  # noqa: E402
import citations as CI  # noqa: E402
import comparer_objets as CO  # noqa: E402
import faux_projet  # noqa: E402
import resoudre_hashes as RH  # noqa: E402
import scenario_registre as S  # noqa: E402

STATS = 'settings/hp_settings/equipment_stats_ui.bin'
A1, A2 = S.AVAILABLE[0], S.AVAILABLE[1]
U1 = S.ABSENT[1]


def stats_adf() -> bytes:
    return AM.build([(A1, S.h32(A1[:-3] + '_name'), 7), (A2, 0x1234, 8)], [S.h32(U1), 5])


class TestAdf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_adf_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, [
            ('settings/hp_settings/equipment_data.bin', S.equipment_data()), (STATS, stats_adf())])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_dump(self, *extra):
        out = Path(self.tmp.name) / 'dump.txt'
        AD.main([self.project, STATS, '--out', str(out)] + list(extra))
        return out.read_text(encoding='utf-8')

    def test_tree(self):
        text = self.run_dump()
        self.assertIn('Rows : Array of Structure Row [2 elements]', text)
        self.assertIn('Name = "{}"'.format(A1), text)
        # StringHash nomme grace a la chaine trouvee par le scan dans equipment_data.bin
        self.assertIn('# "{}"'.format(A1[:-3] + '_name'), text)
        self.assertIn('= hash de "{}"'.format(U1), text)   # entier du tableau Ids egal au hash32 d'un objet

    def test_contains_shows_row(self):
        text = self.run_dump('--contient', A1, '--contient', U1)
        self.assertIn('"{}" : 1 citation(s)'.format(A1), text)
        self.assertIn('sheet.Rows[0].Name', text)
        self.assertIn('ligne sheet.Rows[0]', text)
        self.assertIn('Value = 7', text)
        self.assertIn('sheet.Ids[0]', text)
        self.assertIn('hash32', text)

    def test_offset(self):
        vfs = _projet.open_project(self.project)
        _kind, adf = RH.FileReader(vfs).decode(STATS)
        leaf = [l for l in CI.adf_leaves(adf) if l.path == 'sheet.Rows[1].Value'][0]
        text = self.run_dump('--offset', hex(leaf.offset + 2))
        self.assertIn('sheet.Rows[1].Value = 8', text)

    def test_resolver_collects_unnamed_stringhash(self):
        vfs = _projet.open_project(self.project)
        _kind, adf = RH.FileReader(vfs).decode(STATS)
        t = RH.Targets()
        RH.collect_adf(adf, 'stats', t)
        self.assertIn((32, 0x1234), t.items)
        self.assertEqual(t.items[(32, 0x1234)].contexts[0], 'stats sheet.Rows[1].Key')

    def test_comparer_reference_section(self):
        out = Path(self.tmp.name) / 'cmp.md'
        CO.main([self.project, A1, A2, U1, '--fichier', STATS, '--out', str(out)])
        text = out.read_text(encoding='utf-8')
        section = text.split('## 2.')[1].split('## 3.')[0]
        self.assertIn('equipment_stats_ui.bin (ADF)', section)
        self.assertIn('1 : sheet.Rows[0].Name (texte)', section)
        self.assertIn('1 : sheet.Ids[0] (hash32)', section)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
