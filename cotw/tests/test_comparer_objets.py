#!/usr/bin/env python3
"""
Tests de cotw/comparer_objets.py (fonctions sans projet DECA), sur les arbres de test de cloner_munition.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_comparer_objets.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import comparer_objets as K  # noqa: E402
import test_cloner_munition as T  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402

ITEMS = [T.SRC, 'equipment_ammo_308_pt_01', 'equipment_ammo_absente_01']


class TestComparer(unittest.TestCase):
    def setUp(self):
        self.eq = W.rtpc_from_bytes(T.equipment_tree())
        self.sp = W.rtpc_from_bytes(T.spawn_tree())

    def rows_by_label(self, rows):
        return {r[0]: r[1:] for r in rows}

    def test_equipment_rows(self):
        rows = self.rows_by_label(K.equipment_rows(self.eq, ITEMS, {}))
        self.assertEqual(rows['chemin dans le fichier'][2], 'ABSENT')
        price = next(v for k, v in rows.items() if k.startswith('0x33df3b2f'))
        self.assertEqual(price[:2], ['0 (0x00000000)', '900 (0x00000384)'])
        self.assertEqual(price[2], K.MISSING)
        compat = next(v for k, v in rows.items() if k.startswith('enfant 0x77c2b3ba'))
        self.assertTrue(compat[0].startswith('3 entree(s)'))
        self.assertTrue(compat[1].startswith('1 entree(s)'))

    def test_spawn_rows(self):
        rows = self.rows_by_label(K.spawn_rows({'settings/spawn_ammunition_defs.bin': self.sp}, ITEMS))
        entity = rows['spawn_ammunition_defs.bin : entite']
        self.assertIn('243_sp_01.ee', entity[0])
        self.assertEqual(entity[2], K.MISSING)

    def test_markdown_marks_differences(self):
        lines = K.markdown_table(['a', 'b'], [['pareil', 'x', 'x'], ['different', 'x', 'y']])
        self.assertTrue(lines[2].startswith('|  |'))
        self.assertTrue(lines[3].startswith('| ≠ |'))

    def test_long_path_keeps_its_end(self):
        path = 'editor\\\\entities\\\\hp_weapons\\\\ammunition\\\\rifles\\\\equipment_ammo_243_sp_01.ee'
        self.assertTrue(K.cell(path, 40).endswith('243_sp_01.ee'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
